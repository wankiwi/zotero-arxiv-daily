"""Bounded full-window coverage diagnosis, aggregate output only; no delivery."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from hydra import compose, initialize_config_dir
from loguru import logger
from validate_openreview import ProbeClient
from zot2dailypaper.retriever.openreview_retriever import OpenReviewRetriever


def main():
    logger.disable('zot2dailypaper')
    with initialize_config_dir(config_dir=str(Path(__file__).resolve().parents[1]/'config'),version_base=None):
        config=compose(config_name='interests')
    r=OpenReviewRetriever(config);client=ProbeClient(max_requests=160)
    try:
        r._authenticate(client)
        if not r.authenticated:raise RuntimeError('Authentication required')
        now=datetime.now(timezone.utc);since=now.replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=r.days)
        r.since,r.until=since,now
        print(json.dumps({'stage':'login','http_status':client.last_status,'window_start':since.isoformat(),'window_end':now.isoformat()}),flush=True)
        totals=Counter();seen=set()
        for venue in ['TMLR','ICLR','NeurIPS','ICML','CoRL']:
            client.stage=venue;stats=Counter();historical=0;groups=list(r._groups(client,venue,now.year))
            if not groups:raise RuntimeError('Missing supported venue groups')
            for group,invitation in groups:
                offset=0;previous=None
                # No unsupported date query: older submissions can become public later.
                for page in range(100):
                    data=r._get(client,'/notes',{'invitation':invitation,'limit':1000,'offset':offset,'sort':'tmdate:desc','count':'true'})
                    notes=data.get('notes');count=data.get('count')
                    if not isinstance(notes,list):raise RuntimeError('Malformed notes')
                    fingerprint=tuple(n.get('id') for n in notes)
                    if notes and fingerprint==previous:raise RuntimeError('Pagination did not advance')
                    previous=fingerprint;offset+=len(notes)
                    for n in notes:
                        if not n.get('id') or n['id'] in seen:continue
                        seen.add(n['id']);raw=(venue,group,n)
                        try:
                            r.convert_to_paper(raw,diagnostics=stats)
                            r.since=datetime(1970,1,1,tzinfo=timezone.utc)
                            historical+=r.convert_to_paper(raw) is not None
                        except (ValueError,TypeError,KeyError,OverflowError):stats['malformed']+=1
                        finally:r.since=since
                    if not notes:
                        if isinstance(count,int) and offset<count:raise RuntimeError('Incomplete pagination')
                        break
                    if isinstance(count,int) and offset>=count or count is None and len(notes)<1000:break
                else:raise RuntimeError('Page bound reached')
                print(json.dumps({'stage':'group_complete','venue':venue,'group':group,'records_scanned':offset,'reported_count':count}),flush=True)
            print(json.dumps({'stage':'venue_complete','venue':venue,'counts':dict(stats),'historical_same_topic_matches':historical}),flush=True)
            totals.update(stats);totals['historical_same_topic_matches']+=historical
        print(json.dumps({'status':'success','coverage':'all_returned_supported_venue_groups','request_count':client.calls,'counts':dict(totals)}),flush=True)
        return 0
    except Exception as exc:
        print(json.dumps({'status':'blocked','stage':client.stage,'http_status':client.last_status,'provider_error_category':client.error_category,'error_type':type(exc).__name__,'request_count':client.calls}),flush=True)
        return 1
    finally:client.close()

if __name__=='__main__':raise SystemExit(main())
