#!/opt/kathara-venv/bin/python
"""Per-campaign Kathara entry point: no global config or plugin upgrades."""
import hashlib,importlib,json,pathlib,runpy,sys
ROOT=pathlib.Path('/data/networkcache-concurrency-slides-v5')
def main():
 if len(sys.argv)<2 or sys.argv[1] not in ('lstart','lclean','exec','linfo'):
  raise RuntimeError('only lab-scoped commands are allowed')
 if '-d' not in sys.argv: raise RuntimeError('explicit lab directory required')
 lab=pathlib.Path(sys.argv[sys.argv.index('-d')+1]).resolve()
 if not lab.is_relative_to(ROOT/'lab') and not lab.is_relative_to(ROOT/'diagnostics'):
  raise RuntimeError('lab outside this campaign')
 frozen=json.loads((ROOT/'backend.json').read_text())
 config=ROOT/'kathara.conf'
 if hashlib.sha256(config.read_bytes()).hexdigest()!=frozen['config_sha256']:
  raise RuntimeError('backend config changed')
 setting=importlib.import_module('Kathara.setting.Setting')
 setting.DEFAULT_SETTINGS_PATH=str(config)
 plugin=importlib.import_module('Kathara.manager.docker.DockerPlugin')
 def check(self):
  if self.current_name!=frozen['plugin_name']:raise RuntimeError('wrong network plugin')
  actual=self.client.plugins.get(self.current_name)
  if not actual.enabled or actual.id!=frozen['plugin_id']:
   raise RuntimeError('plugin missing, disabled, or replaced; auto-upgrade forbidden')
 plugin.DockerPlugin.check_and_download_plugin=check
 runpy.run_module('kathara',run_name='__main__')
if __name__=='__main__':main()
