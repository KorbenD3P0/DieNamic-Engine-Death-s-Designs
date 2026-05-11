import json

with open('data/hazards.json', 'r', encoding='utf-8') as f:
    hazards = json.load(f)

missing_spawn = []
missing_examine = []
has_both = []

for hazard_id, hazard_data in sorted(hazards.items()):
    has_spawn = 'spawn_entities' in hazard_data
    has_examine = 'examine_responses' in hazard_data
    
    if not has_spawn:
        missing_spawn.append(hazard_id)
    if not has_examine:
        missing_examine.append(hazard_id)
    if has_spawn and has_examine:
        has_both.append(hazard_id)

print("=" * 80)
print("SPAWN_ENTITIES & EXAMINE_RESPONSES AUDIT")
print("=" * 80)
print()
print(f"✅ Hazards with BOTH spawn_entities and examine_responses: {len(has_both)}")
for h in has_both:
    print(f"  - {h}")

print()
print(f"⚠ Hazards missing spawn_entities: {len(missing_spawn)}")
for h in missing_spawn[:15]:
    print(f"  - {h}")
if len(missing_spawn) > 15:
    print(f"  ... and {len(missing_spawn) - 15} more")

print()
print(f"⚠ Hazards missing examine_responses: {len(missing_examine)}")
for h in missing_examine[:15]:
    print(f"  - {h}")
if len(missing_examine) > 15:
    print(f"  ... and {len(missing_examine) - 15} more")

print()
needs_both = set(missing_spawn) & set(missing_examine)
print(f"🔴 Hazards needing BOTH: {len(needs_both)}")
for h in sorted(needs_both)[:20]:
    print(f"  - {h}")
