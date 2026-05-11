import json

# Load hazards
with open('data/hazards.json', 'r', encoding='utf-8') as f:
    hazards = json.load(f)

# Find hazards without examine actions
missing_examine = []
for hazard_id, hazard_data in hazards.items():
    has_examine = 'examine_actions' in hazard_data or 'player_interaction' in hazard_data
    if not has_examine:
        missing_examine.append(hazard_id)

print(f"Total hazards missing examine: {len(missing_examine)}")
print("\nList:")
for h in sorted(missing_examine):
    name = hazards[h].get('name', h)
    placement = hazards[h].get('placement_object', ['unknown'])
    print(f"  - {h} ('{name}') - placed on: {placement[0] if placement else 'none'}")
