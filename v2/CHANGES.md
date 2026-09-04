# ASTCIE Rewritten / Cleaned Package

## Changes made for this package

1. **Path fixes in WRAPPER.py**
   - Scripts now correctly referenced as `v8/complexity7.py` and `v9/complexity8.py`

2. **Structure**
   - Clean layout: `v8/`, `v9/`, `tests/`, `docs/`
   - Original large result dumps removed to keep package lightweight

3. **Tests**
   - Added `tests/test_basic.py` – smoke tests for V8.1 and V9
   - Synthetic video generator included
   - All tests pass on clean environment

4. **Dependencies**
   - `requirements.txt` added

5. **Verification**
   - Both engines successfully process synthetic video
   - JSON outputs contain expected keys (`v8_fusion_score`, `v9_fusion_score`, `bit_demand_index`)
   - Scores are in valid [0,1] range

## How to use

```bash
pip install -r requirements.txt
python tests/test_basic.py          # should print ALL TESTS PASSED
python v8/complexity7.py path/to/video.mp4
python v9/complexity8.py path/to/video.mp4
python WRAPPER.py path/to/video.mp4 --no-encode
```

Original research README preserved.
