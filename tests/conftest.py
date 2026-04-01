import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SESSION_SECRET", "test_secret_key_for_testing_only_1234567890abcdef")
os.environ.setdefault("DATABASE_URL", "")
