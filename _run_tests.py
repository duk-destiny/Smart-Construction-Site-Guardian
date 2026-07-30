import pytest, sys
targets = sys.argv[1:] or ["tests/"]
rc = pytest.main([*targets, "-q", "--tb=line", "-p", "no:cacheprovider",
                  "--no-header"])
with open("pytest_last.txt", "w", encoding="utf-8") as f:
    f.write(f"PYTEST_RC={rc}\n")
print(f"PYTEST_RC={rc}")
