from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def test_feasibility_launchagent_is_one_shot_and_uses_only_existing_runner():
    wrapper=(ROOT/'tools/run_daily_facts_feasibility_local.sh').read_text()
    plist=(ROOT/'ops/com.asl.daily-facts-feasibility.plist').read_text()
    assert 'cd /Users/luke808/ASL' in wrapper and 'run_daily_facts_phase1_feasibility.py' in wrapper and '--days 5' in wrapper
    assert 'com.asl.daily-facts-feasibility' in plist and 'WorkingDirectory' in plist and 'RunAtLoad' in plist
    assert 'KeepAlive' not in plist and 'mcp' not in plist.lower()
