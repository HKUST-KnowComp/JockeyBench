"""Install the Linux syscall boundary before any untrusted Python is evaluated.

Filesystem confinement comes from bubblewrap, not this filter. Seccomp blocks
networking, new processes, exec and namespace/kernel interfaces while retaining
ordinary Python and numerical-library threads. No optional insecure fallback.
"""

import ctypes
import errno
import platform

# libseccomp's stable public ABI (include/seccomp.h).
ALLOW = 0x7FFF0000
ERRNO = 0x00050000
MASKED_EQ = 7
CTL_NNP = 3
CTL_TSYNC = 4

# clone(2) uses argument zero for flags on both supported architectures.
CLONE_REQUIRED = 0x00000100 | 0x00000200 | 0x00000400 | 0x00000800 | 0x00010000
CLONE_ALLOWED = CLONE_REQUIRED | 0x00040000 | 0x00080000 | 0x00100000 | 0x00200000 | 0x01000000
DENIED_SYSCALLS = (
    'socket',
    'socketpair',
    'socketcall',
    'connect',
    'bind',
    'listen',
    'accept',
    'accept4',
    'sendto',
    'sendmsg',
    'sendmmsg',
    'recvfrom',
    'recvmsg',
    'recvmmsg',
    'fork',
    'vfork',
    'execve',
    'execveat',
    'ptrace',
    'process_vm_readv',
    'process_vm_writev',
    'pidfd_open',
    'pidfd_getfd',
    'pidfd_send_signal',
    'setsid',
    'setpgid',
    'mount',
    'umount',
    'umount2',
    'pivot_root',
    'chroot',
    'unshare',
    'setns',
    'fsopen',
    'fsconfig',
    'fsmount',
    'fspick',
    'open_tree',
    'move_mount',
    'mount_setattr',
    'open_by_handle_at',
    'name_to_handle_at',
    'io_uring_setup',
    'io_uring_enter',
    'io_uring_register',
    'bpf',
    'perf_event_open',
    'userfaultfd',
    'keyctl',
    'add_key',
    'request_key',
    'reboot',
    'kexec_load',
    'kexec_file_load',
    'init_module',
    'finit_module',
    'delete_module',
    'acct',
    'syslog',
    'quotactl',
    'swapon',
    'swapoff',
    'iopl',
    'ioperm',
)


class Comparison(ctypes.Structure):
    _fields_ = [
        ('arg', ctypes.c_uint),
        ('op', ctypes.c_int),
        ('datum_a', ctypes.c_uint64),
        ('datum_b', ctypes.c_uint64),
    ]


def install():
    """Load a persistent filter or raise before executing any submitted code."""
    if platform.machine().lower() not in {'x86_64', 'amd64', 'aarch64', 'arm64'} or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError('Linux isolation supports 64-bit x86_64 and aarch64 runtimes')
    try:
        lib = ctypes.CDLL('libseccomp.so.2', use_errno=True)
    except OSError as exc:
        raise RuntimeError('Linux isolation requires libseccomp.so.2 (install libseccomp2)') from exc
    lib.seccomp_init.argtypes = [ctypes.c_uint32]
    lib.seccomp_init.restype = ctypes.c_void_p
    lib.seccomp_release.argtypes = [ctypes.c_void_p]
    lib.seccomp_release.restype = None
    lib.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    lib.seccomp_syscall_resolve_name.restype = ctypes.c_int
    lib.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(Comparison)]
    lib.seccomp_rule_add_array.restype = ctypes.c_int
    lib.seccomp_attr_set.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
    lib.seccomp_attr_set.restype = ctypes.c_int
    lib.seccomp_load.argtypes = [ctypes.c_void_p]
    lib.seccomp_load.restype = ctypes.c_int
    context = lib.seccomp_init(ALLOW)
    if not context:
        raise RuntimeError('Could not allocate the Linux syscall filter')

    def check(result):
        if result != 0:
            raise RuntimeError('Could not install the Linux syscall filter (libseccomp status ' + str(result) + ')')

    def deny(name, error=errno.EPERM, comparison=None, required=False):
        number = lib.seccomp_syscall_resolve_name(name.encode('ascii'))
        if number == -1:
            raise RuntimeError('libseccomp does not recognize required syscall ' + name)
        if number < 0:
            # Negative pseudo numbers represent syscalls absent on this native
            # architecture (e.g. fork on aarch64), not unfiltered real syscalls.
            if required:
                raise RuntimeError('Missing required Linux syscall ' + name)
            return
        check(
            lib.seccomp_rule_add_array(
                context, ERRNO | error, number, int(comparison is not None), ctypes.byref(comparison) if comparison is not None else None
            )
        )

    try:
        check(lib.seccomp_attr_set(context, CTL_NNP, 1))
        check(lib.seccomp_attr_set(context, CTL_TSYNC, 1))
        for name in DENIED_SYSCALLS:
            deny(name)
        # clone3 stores flags behind a pointer, beyond seccomp's argument
        # inspection. ENOSYS makes current glibc fall back to inspected clone.
        deny('clone3', error=errno.ENOSYS, required=True)
        for bit in range(64):
            mask = 1 << bit
            if CLONE_REQUIRED & mask:
                deny('clone', comparison=Comparison(0, MASKED_EQ, mask, 0), required=True)
            elif not CLONE_ALLOWED & mask:
                deny('clone', comparison=Comparison(0, MASKED_EQ, mask, mask), required=True)
        # Keep bubblewrap's parent-death signal in force. PR_SET_NAME and other
        # innocuous thread controls remain available to numerical libraries.
        deny('prctl', comparison=Comparison(0, MASKED_EQ, 0xFFFFFFFF, 1), required=True)
        check(lib.seccomp_load(context))
    finally:
        lib.seccomp_release(context)
