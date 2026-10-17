"""Load the audited run-bound framework under an isolated module namespace."""
import hashlib,importlib,importlib.util,json,sys
from pathlib import Path

CLOCK=1704456000
ORIGIN='2024-01-01'
HORIZON='2024-01-06'
ARCHIVE_HASH='d545ca9a936418de50bf6ad2d8ecf67c11226bc8744cfdffb9e82016b355c392'
NAME='_policy_warmup_v060'

def historical_views(connection):
    # TEMP views live in memory on a mode=ro main database. They also constrain
    # trusted broad scans (e.g. the archived connections index) before projection.
    connection.execute('PRAGMA query_only=OFF')
    try:
        connection.execute('PRAGMA temp_store=MEMORY')
        connection.executescript(f'''
        CREATE TEMP VIEW races AS SELECT * FROM main.races WHERE day<'{HORIZON}' AND pre_at<={CLOCK};
        CREATE TEMP VIEW entries AS SELECT e.* FROM main.entries e JOIN main.races r ON r.id=e.race_id WHERE r.day<'{HORIZON}' AND r.pre_at<={CLOCK};
        CREATE TEMP VIEW expanded_race_ids AS SELECT e.* FROM main.expanded_race_ids e JOIN main.races r ON r.id=e.race_id WHERE r.day<'{HORIZON}' AND r.pre_at<={CLOCK};
        ''')
    finally:connection.execute('PRAGMA query_only=ON')

def load(profile):
    source=Path(profile['archive_source']).resolve()
    hashes={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*.py')}
    digest=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    if digest!=ARCHIVE_HASH or profile['clock']!=CLOCK or profile['origin']!=ORIGIN or profile['horizon']!=HORIZON:
        raise ValueError('Audited warmup boundary/source mismatch')
    if NAME not in sys.modules:
        spec=importlib.util.spec_from_file_location(NAME,source/'__init__.py',submodule_search_locations=[str(source)])
        module=importlib.util.module_from_spec(spec);sys.modules[NAME]=module;spec.loader.exec_module(module)
    elif Path(sys.modules[NAME].__file__).resolve()!=source/'__init__.py':
        raise ValueError('Archive namespace already bound elsewhere')
    return lambda name:importlib.import_module(NAME+'.'+name)

def support_types(mod):
    Rejected=mod('common').Rejected
    class Source(mod('economic_data').EconomicSource):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            historical_views(self.con)
        def schedule(self,*args,**kwargs):
            # Clock anchor only. No formal card, schedule scan or outcome read.
            return [{'day':HORIZON,'races':[]}]
        def race(self,public,clock):
            if clock!=CLOCK:raise Rejected('Warmup clock is frozen')
            rid=self.raw_race(public)
            if not self.con.execute('SELECT 1 FROM races WHERE id=? AND day<?',(rid,HORIZON)).fetchone():
                raise Rejected('Only pre-horizon historical support')
            return super().race(public,clock)
        def history_page(self,clock,online_start,released,after,limit,anchor=None,filters=None,order='recent',scope='all',**kwargs):
            if clock!=CLOCK or online_start!=ORIGIN:raise Rejected('Support boundary mismatch')
            filters=dict(filters or {})
            maximum=filters.get('day_max',-1)
            if type(maximum) is not int or maximum>=0:raise Rejected('Support requires day_max < 0 relative to 2024-01-01')
            filters['day_max']=maximum
            return super().history_page(clock,online_start,{},after,limit,anchor=anchor,filters=filters,order=order,scope='support',**kwargs)
    return Source
