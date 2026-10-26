import os

# never scan the machine running the tests (boop starts a background folder index otherwise)
os.environ["BOOP_NO_INDEX"] = "1"
