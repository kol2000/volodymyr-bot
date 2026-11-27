import contextlib,io,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import football_forecast_v8_ligue1_register_v2 as reg

class Ligue1RollingTests(unittest.TestCase):
 def test_register_second_batch_preserves_originals(self):
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);dest=root/'observations';dest.mkdir();history={'provider_id':'historic','matches':{}}
   forecasts=[]
   for i in range(19):
    mid=str(i);f=dict(competition='Ligue1',match_id=mid,home='Home '+mid,away='Away '+mid,date='2099-01-01',production_eligible=False)
    forecasts.append(f);history['matches'][mid]=dict(date=f['date'],time='20:00',time_source='open_matchday',time_status='UNVERIFIED')
   for num,subset in enumerate((forecasts[:18],forecasts[18:])):
    batch=dict(schema='bubus_v8_europe_research_batch_v1',competition='Ligue1',production_eligible=False,forecasts=subset,created_utc='2020-01-01T00:00:00+00:00',snapshot_id='snapshot')
    name=f'batch_{num}.json';raw=json.dumps(batch).encode();(root/name).write_bytes(raw)
    if num==0:
     for f in subset:
      mid=f['match_id'];obs=dict(schema='bubus_v8_ligue1_date_observation_v1',competition='Ligue1',match_id=mid,production_eligible=False,forecast=f,forecast_batch_file=name,forecast_batch_sha256=reg.sha(raw),forecast_snapshot_id='snapshot',forecast_created_utc=batch['created_utc'],registered_utc='2020-01-02T00:00:00+00:00',forecast_date=f['date'],kickoff_candidate_utc=None,kickoff_verified=False,provider_id='historic',provider_date=f['date'],provider_time_raw='20:00',provider_time_source='open_matchday',provider_time_status='UNVERIFIED')
      (dest/('ligue1_'+reg.sha(f'Ligue1:{mid}'.encode())[:24]+'.json')).write_text(json.dumps(obs))
   original={p.name:p.read_bytes() for p in dest.glob('*.json')}
   with patch.object(reg,'FOLDER',root),patch.object(reg,'DEST',dest),patch.object(reg,'historical_provider',return_value=dict(history,observation_file='test')),patch.object(sys,'argv',['register','--write']),contextlib.redirect_stdout(io.StringIO()):
    reg.main();reg.main()
   self.assertEqual(len(list(dest.glob('*.json'))),19)
   for name,data in original.items():self.assertEqual((dest/name).read_bytes(),data)
if __name__=='__main__':unittest.main()
