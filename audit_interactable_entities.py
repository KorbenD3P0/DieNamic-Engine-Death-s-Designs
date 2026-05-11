import json
import re

# Load hazards
with open('data/hazards.json', 'r', encoding='utf-8') as f:
    hazards = json.load(f)

# Common object keywords that suggest interactable entities
ENTITY_KEYWORDS = [
    'wire', 'cord', 'cable', 'valve', 'lever', 'button', 'switch', 'knob',
    'tank', 'pipe', 'hose', 'pump', 'gauge', 'meter', 'panel', 'console',
    'door', 'gate', 'latch', 'lock', 'bolt', 'handle', 'wheel', 'chain',
    'lamp', 'light', 'bulb', 'outlet', 'socket', 'plug', 'fuse', 'breaker',
    'beam', 'support', 'column', 'scaffolding', 'railing', 'ladder', 'platform',
    'machine', 'device', 'equipment', 'apparatus', 'mechanism', 'generator'
]

def extract_mentioned_entities(text):
    """Extract potential entity nouns from description text."""
    text_lower = text.lower()
    mentioned = []
    for keyword in ENTITY_KEYWORDS:
        # Look for the keyword with common adjectives/articles
        pattern = r'\b(?:the |a |an )?(?:[\w-]+ )?(' + keyword + r's?)\b'
        matches = re.findall(pattern, text_lower)
        if matches:
            mentioned.extend(matches)
    return list(set(mentioned))

def check_hazard_interactability(hazard_id, hazard_data):
    """Check if hazard has interactable entities for all mentioned objects."""
    issues = []
    
    # Get all targets from examine_actions and player_interaction
    examine_targets = set()
    interaction_targets = set()
    
    # Collect examine targets
    examine_actions = hazard_data.get('examine_actions', {})
    for state_key, actions in examine_actions.items():
        if isinstance(actions, list):
            for action in actions:
                targets = action.get('targets', [])
                examine_targets.update([t.lower() for t in targets])
    
    # Collect player_interaction targets
    player_interaction = hazard_data.get('player_interaction', {})
    for verb, interactions in player_interaction.items():
        for interaction in interactions:
            targets = interaction.get('on_target_name', [])
            examine_targets.update([t.lower() for t in targets])
    
    # Get object_name_options and placement_object
    object_names = set([name.lower() for name in hazard_data.get('object_name_options', [])])
    placement = set([obj.lower() for obj in hazard_data.get('placement_object', [])])
    
    all_targets = examine_targets | interaction_targets | object_names | placement
    
    # Check each state description for mentioned entities
    states = hazard_data.get('states', {})
    for state_id, state_data in states.items():
        desc = state_data.get('description', '')
        mentioned = extract_mentioned_entities(desc)
        
        for entity in mentioned:
            # Check if this entity has a corresponding target
            found = False
            for target in all_targets:
                if entity in target or target in entity:
                    found = True
                    break
            
            if not found:
                issues.append({
                    'state': state_id,
                    'entity': entity,
                    'context': desc[:80] + '...' if len(desc) > 80 else desc
                })
    
    return issues

# Audit all hazards
print("HAZARD INTERACTABILITY AUDIT")
print("=" * 80)
print()

hazards_with_issues = []

for hazard_id, hazard_data in sorted(hazards.items()):
    issues = check_hazard_interactability(hazard_id, hazard_data)
    
    if issues:
        hazards_with_issues.append(hazard_id)
        print(f"⚠ {hazard_id} ({hazard_data.get('name', hazard_id)})")
        print(f"  Missing interactable entities:")
        for issue in issues[:3]:  # Show first 3 issues
            print(f"    - '{issue['entity']}' in state '{issue['state']}'")
            print(f"      Context: {issue['context']}")
        if len(issues) > 3:
            print(f"    ... and {len(issues) - 3} more entities")
        print()

print("=" * 80)
print(f"Total hazards with missing entities: {len(hazards_with_issues)}")
print(f"Hazards needing entity additions: {', '.join(hazards_with_issues[:10])}")
if len(hazards_with_issues) > 10:
    print(f"  ... and {len(hazards_with_issues) - 10} more")
