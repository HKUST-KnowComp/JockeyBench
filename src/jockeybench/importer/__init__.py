"""Import of privately acquired JRDB archives into the database the runtime reads.

Pipeline: ``manifest`` (hash acquired archives) -> ``build`` (decode fixed-width
records into SQLite) -> ``anonymize`` (pseudonymise identifiers with a private
key). Nothing here runs during a trajectory.
"""
