import json
from schema import Profile
from pydantic import ValidationError

data = json.load(open("CV.json"))
try:
    profile = Profile.model_validate(data)
    print(f"OK — {len(profile.all_ids())} ids")
except ValidationError as e:
    print(e)

