# Ensures the tests/ dir is importable so `import sse_fixture` resolves
# from tests/src/consumer/ test modules (pytest prepend mode normally
# inserts conftest dirs; this is belt-and-braces).
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
