# schemas.py
"""
The Tablets of Law.

Defines the data structures for all game entities using TypedDict for schema validation.
This is the single source of truth for data integrity, ensuring that all Scrolls of Destiny
(JSON files) adhere to the Architect's Design.
"""
from typing import List, Dict, Any, Union, Set

try:
    from typing import TypedDict, NotRequired
except ImportError:
    from typing_extensions import TypedDict, NotRequired

# --- CORE CONFIGURATION SCHEMAS ---

class GameConfigTypedDict(TypedDict, total=False):
    GAME_NAME: str
    GAME_VERSION: str
    INITIAL_TURNS: int
    MAX_SAVE_SLOTS: int
    WEIGHT_CATEGORIES: Dict[str, float]

class ConstantsTypedDict(TypedDict, total=False):
    UI_THRESHOLDS: Dict[str, float]
    pass

class QTEDefinitionTypedDict(TypedDict, total=False):
    name: str
    input_type: str
    valid_responses: List[str]
    default_duration: Union[int, float]

# --- NEW: Specific TypedDicts for Hazard Interactions ---
class PlayerInteractionRuleTypedDict(TypedDict, total=False):
    on_target_name: Union[str, List[str]]
    requires_hazard_state: List[str]
    qte_to_trigger: str
    
    on_success: Dict[str, Union[str, int, float, bool]]
    on_failure: Dict[str, Union[str, int, float, bool]]
    
    message: str
    blocks_action_success: bool
    # NEW: Support for class affinities
    requires_affinity: str

class RoomActionTriggerRuleTypedDict(TypedDict, total=False):
    action_verb: str
    on_target_name: str
    requires_hazard_state: List[str]
    effect_on_self: Dict[str, Any]
    blocks_action_success: bool

# --- NEW: Hyper-Specific SCHEMAS for Nested Data ---

class QTEContextTypedDict(TypedDict, total=False):
    """Defines the highly variable structure of a QTE's context data."""
    ui_prompt_message: str
    expected_input_word: str
    success_message: str
    failure_message_wrong_input: str
    failure_message_timeout: str
    is_fatal_on_failure: bool
    next_state_after_qte_success: str
    next_state_after_qte_failure: str
    next_state_after_timeout: str
    # For branching QTEs
    expected_input_options: List[str]
    input_to_next_state: Dict[str, str]
    success_messages: List[str]
    # For custom logic
    qte_source_hazard_id: str
# --- NEW: NPC Intervention Keys ---
    npc_fatal_on_failure: bool
    target_npc: str
    # --- NEW: per-character tunables (allow scalar or {"default":X,"EMT":Y}) ---
    target_mash_count: Union[int, Dict[str, int]]
    required_tap_count: Union[int, Dict[str, int]]
    required_hold_time: Union[float, Dict[str, float]]
    target_alternations_default: Union[int, Dict[str, int]]
    target_beats: Union[int, Dict[str, int]]
    keys_default: List[str]
    required_sequence: List[str]
    required_code: List[str]

class QTETriggerTypedDict(TypedDict, total=False):
    """Defines the structure for a QTE trigger within a hazard state."""
    qte_type: Union[str, List[str]]
    duration: Union[int, float]
    qte_context: QTEContextTypedDict

class UIPopupEventTypedDict(TypedDict, total=False):
    """Defines the structure for a UI popup event."""
    type: str
    title: str
    text: str

class RoomActionEffectTypedDict(TypedDict, total=False):
    """Defines the effects of a room action trigger."""
    target_state: str
    ui_popup_event: UIPopupEventTypedDict # Now using our precise law

class RoomActionTriggerRuleTypedDict(TypedDict, total=False):
    action_verb: str
    on_target_name: str
    requires_hazard_state: List[str]
    # REFINED: Uses the new specific effect schema
    effect_on_self: RoomActionEffectTypedDict
    blocks_action_success: bool

class EnvironmentalEffectTypedDict(TypedDict, total=False):
    noise_level: Union[int, str]
    is_sparking: bool
    visibility: str
    temperature_celsius: int
    gas_level: int
    is_electrified: bool
    is_on_fire: bool

class FurnitureUseInteractionRuleTypedDict(TypedDict, total=False):
    item_names_required: List[str]
    action_effect: str
    message_success: str
    message_fail_item: str
    mri_states_can_deactivate: List[str]
    message_fail_mri_state: str

class FurnitureOnBreakSpillItem(TypedDict):
    name: str
    quantity: Union[int, str]

# --- GAMEPLAY OBJECT SCHEMAS ---

class ItemTypedDict(TypedDict):
    name: str
    description: str
    type: str
    tags: NotRequired[List[str]]
    examine_details: NotRequired[str]
    subtype: NotRequired[str]
    level: NotRequired[Union[int, str, List[int]]]
    weight: NotRequired[Union[str, float, int]]
    takeable: NotRequired[bool]
    is_hidden: NotRequired[bool]
    is_evidence: NotRequired[bool]
    is_critical: NotRequired[bool]
    is_flammable: NotRequired[bool]
    is_metallic: NotRequired[bool]
    is_distributable_in_containers: NotRequired[bool]
    consumable_on_use: NotRequired[bool]
    unlocks: NotRequired[List[str]]
    use_on: NotRequired[List[str]]
    use_result: NotRequired[Dict[str, str]]
    trigger_hazard_on_action: NotRequired[Dict[str, str]]
    character_connection: NotRequired[str]
    special_property: NotRequired[str]

class FurnitureTypedDict(TypedDict):
    name: str
    description: str
    is_container: NotRequired[bool]
    locked: NotRequired[bool]
    capacity: NotRequired[int]
    items: NotRequired[List[Union[str, Dict[str, Any]]]]  # Allow both strings and dictionaries
    is_metallic: NotRequired[bool]
    is_breakable: NotRequired[bool]
    break_integrity: NotRequired[int]
    on_break_success_message: NotRequired[str]
    on_break_spill_items: NotRequired[List[FurnitureOnBreakSpillItem]]
    use_item_interaction: NotRequired[List[FurnitureUseInteractionRuleTypedDict]]
    unlocks_with_item: NotRequired[str]

class RoomObjectTypedDict(TypedDict, total=False):
    name: str
    id_key: str
    description: str
    is_omen_provider: NotRequired[str]
    aliases: NotRequired[List[str]]

class RoomTypedDict(TypedDict):
    description: str
    exits: Dict[str, Union[str, Dict[str, Any]]]
    examine_details: NotRequired[Dict[str, str]]
    furniture: NotRequired[List[Union[str, FurnitureTypedDict]]]  # Allow both strings and FurnitureTypedDict
    objects: NotRequired[List[Union[str, RoomObjectTypedDict]]]
    items_present: NotRequired[List[str]]
    hazards_present: NotRequired[List[Union[str, Dict[str, Any]]]]
    possible_hazards: NotRequired[List[Union[str, Dict[str, Any]]]]
    first_entry_text: NotRequired[Union[str, None]]
    state_descriptions: NotRequired[Dict[str, str]]
    state_examine_details: NotRequired[Dict[str, Dict[str, str]]]
    state_added_objects: NotRequired[Dict[str, List[Union[str, Dict[str, Any]]]]]
    floor: NotRequired[int]
    locked: NotRequired[bool]
    unlocks_with: NotRequired[Union[str, List[str], None]]
    forceable: NotRequired[bool]
    force_threshold: NotRequired[int]
    npcs: NotRequired[List[Union[str, Dict[str, Any]]]]
    npcs_present: NotRequired[List[Union[str, Dict[str, Any]]]]

# --- NPC & DIALOGUE SCHEMAS ---

class NPCDialogueOptionTypedDict(TypedDict, total=False):
    text: str
    target_state: str

class NPCDialogueStateTypedDict(TypedDict, total=False):
    text: str
    next_state: str
    options: List[NPCDialogueOptionTypedDict]
    on_talk_action: Dict[str, Any]

class NPCTypedDict(TypedDict, total=False):
    id: str
    name: str
    description: str
    examinable: bool
    initial_state: str
    dialogue_states: Dict[str, NPCDialogueStateTypedDict]
    action_verb: str  

# --- PERSISTENT CAST SCHEMAS ---

# The new global archetype dictionary (e.g., "act_4_the_plan" -> "helper" -> dialogue node)
ArchetypeDialogueTypedDict = Dict[str, Dict[str, Any]]

# The flattened master NPC dictionary
MasterNPCsTypedDict = Dict[str, Any]

class NPCsFileTypedDict(TypedDict, total=False):
    """
    Root schema for the modernized npcs.json. 
    """
    master_npcs: MasterNPCsTypedDict
    archetype_dialogue: ArchetypeDialogueTypedDict

# --- HAZARD & CHALLENGE SCHEMAS ---

class SabotageDefinitionTypedDict(TypedDict):
    required_tool: str
    success_chance: float
    success_state: str
    success_message: str
    failure_state: str
    failure_message: str
    failure_damage: NotRequired[int]

# --- MAIN SCHEMAS ---

class HazardStateTypedDict(TypedDict, total=False):
    description: str
    environmental_effect: EnvironmentalEffectTypedDict
    triggers_qte_on_entry: QTETriggerTypedDict
    # --- NEW: NPC Rescue QTE ---
    npc_intervention: QTETriggerTypedDict
    chance_to_progress: Union[int, float]
    next_state: str
    instant_death_in_room: bool
    death_message: str
    on_state_entry_special_action: Union[str, Dict[str, Any]]  # <-- Allow dict or str
    is_terminal_state: bool
    autonomous_action: str
    duration_in_state: Union[int, float]
    progression_condition: NotRequired[Dict[str, Any]]
    masks_sensory_feedback: NotRequired[bool]

class HazardTypedDict(TypedDict, total=False):
    name: str
    initial_state: str
    states: Dict[str, HazardStateTypedDict]
    sabotage: Dict[str, Any]
    object_name_options: List[str]
    player_interaction: Dict[str, List[PlayerInteractionRuleTypedDict]]
    triggered_by_room_action: List[RoomActionTriggerRuleTypedDict]
    can_move_between_rooms: bool

# --- NARRATIVE & CHARACTER SCHEMAS ---

class CharacterAffinityTypedDict(TypedDict, total=False):
    # Which item "types" or "subtypes" give bonuses?
    item_types: List[str] 
    # Which hazard "tags" can they sense or counter?
    hazard_tags: List[str]
    # Which disaster "tags" do they have lore knowledge of?
    disaster_tags: List[str]
    # Specific actions they excel at (e.g., "force", "pick_lock")
    skilled_actions: List[str]

class OmenDataTypedDict(TypedDict, total=False):
    text: str
    sfx: str
    color_sting: str  # NEW: Hex color code (e.g., "ff0000" for red)

class DeathStatsTypedDict(TypedDict, total=False):
    D: int
    E: int
    A: int
    T: int
    H: int

class CharacterClassTypedDict(TypedDict, total=False):
    description: str
    max_hp: int
    stats: DeathStatsTypedDict
    observations: Dict[str, str]
    affinities: Dict[str, List[str]]
    class_flags: Dict[str, bool]
    special_mechanics: List[str]
    
class DisasterTypedDict(TypedDict):
    name: NotRequired[str]
    description: str
    killed_count: Union[Dict[str, int], int]
    warnings: List[str]
    related_evidence: NotRequired[List[str]]
    death_narrative: NotRequired[str]
    environmental_omens: NotRequired[Dict[str, Union[str, List[str], OmenDataTypedDict]]]

# Add this new schema for evaluating branches
class ConditionalTransitionTypedDict(TypedDict, total=False):
    condition: str          # e.g., "has_item", "has_flag", "has_companion", "default"
    item_name: Union[str, List[str]]          # Only required if condition == "has_item"
    flag_name: str          # Only required if condition == "has_flag"
    companion_name: str     # Only required if condition == "has_companion"
    next_level_id: str      # The level to load if true
    next_level_start_room: str # Optional: Override the entry room

# Update the existing LevelRequirement schema
class LevelRequirementTypedDict(TypedDict, total=False):
    exit_room: str
    auto_complete_on_disaster_view: bool
    
    # Legacy linear keys (now optional so the engine stops crashing)
    next_level_id: str
    next_level_start_room: str
    
    # New branching keys
    conditional_transitions: List[ConditionalTransitionTypedDict]

class EvidenceSourceTypedDict(TypedDict):
    backstory: str
    evidence_list: list[str]

# --- NEW: Specific Schema for Complex Synergy Logic ---
class ComplexSynergyTypedDict(TypedDict, total=False):
    catalysts: List[str]
    result: str
    message: str

# --- UPDATED: The Synergy Law ---
class HazardSynergiesTypedDict(TypedDict, total=False):
    # We use Union to allow the old ["fire", "electrical"] format 
    # AND the new {"catalysts": [...], "result": "..."} format.
    water: Union[List[str], ComplexSynergyTypedDict]
    electrical: Union[List[str], ComplexSynergyTypedDict]
    fire: Union[List[str], ComplexSynergyTypedDict]
    flammable_gas: Union[List[str], ComplexSynergyTypedDict]
    flammable_liquid: Union[List[str], ComplexSynergyTypedDict]
    obstruct: Union[List[str], ComplexSynergyTypedDict]
    slip_and_fall: Union[List[str], ComplexSynergyTypedDict]

class StatusEffectsFileTypedDict(TypedDict, total=False):
    status_effects_definitions: dict[str, Any]
    VISIBILITY_LEVELS_SEVERITY: dict[str, int]
    SMELL_LEVELS_PRIORITY: dict[str, int]

class SurvivorFatesFileTypedDict(TypedDict):
    fates: list[str]

class TemperatureMappingsFileTypedDict(TypedDict, total=False):
    normal: int
    cold: int
    freezing: int
    hot: int

class VisionariesFileTypedDict(TypedDict, total=False):
    strangers_distinctive: list[str]
    children_youths: list[str]
    family_friends: list[str]
    emergency_services: list[str]
    service_workers_venue: list[str]

# --- PLAYER STATE & PROGRESS SCHEMAS ---

class PlayerTypedDict(TypedDict):
    location: str
    # inventory is treated as a dict by the engine when awarding items
    inventory: Union[Dict[str, Any], List[Union[str, Dict[str, Any]]]]
    hp: int
    max_hp: int
    fear: float
    score: int
    turns_left: int
    actions_taken: int
    visited_rooms: Set[str]
    current_level: int
    character_class: str
    status_effects: Dict[str, int]
    qte_active: Union[bool, str]
    qte_context: Dict[str, Any]
    qte_duration: float
    intro_disaster: Dict[str, Any]

class NPCInterventionQTETypedDict(TypedDict, total=False):
    """
    Defines a QTE triggered when the player witnesses a hazard targeting an NPC
    and chooses to intervene, or when an NPC steps in front of a hazard aimed
    at the player.
    """
    qte_type: str                          # e.g. "button_mash", "sequence"
    duration: Union[int, float]
    qte_context: "QTEContextTypedDict"
 
class HazardNPCTargetingTypedDict(TypedDict, total=False):
    """
    Controls how a hazard hunts NPCs on Death's List.
    Add this block to any HazardTypedDict under key "npc_targeting".
    """
    # How many turns between re-evaluating who to target (default: 1)
    retarget_interval: int
 
    # Message shown in output panel when the hazard locks onto an NPC.
    # Supports {npc_name} placeholder.
    target_acquired_message: str
 
    # If True, the hazard only activates when an NPC (or the player) is next.
    # Otherwise it may activate even without a live target.
    requires_live_target: bool
 
    # QTE presented to the player when the NPC is about to be killed.
    # Player can choose to intervene (saving the NPC) or let it happen.
    intervention_qte: NPCInterventionQTETypedDict
 
    # QTE presented when an NPC jumps in front of a hazard aimed at the player.
    # Resolving this QTE saves the player; failure kills the NPC AND the player
    # takes splash damage.
    npc_sacrifice_qte: NPCInterventionQTETypedDict
 
    # Popup shown when the player successfully saves the NPC.
    on_npc_saved_popup: str    # supports {npc_name}
 
    # Popup shown when the NPC dies (player did not intervene / QTE failed).
    on_npc_killed_popup: str   # supports {npc_name}
 
    # HP damage dealt to the player if they fail the sacrifice QTE
    # (NPC takes the kill hit but player gets splash).
    player_splash_damage: int

class AchievementTypedDict(TypedDict):
    name: str
    unlocked: bool
    icon: str
    description: str

class PlayerAchievementsFileTypedDict(TypedDict):
    achievements: Dict[str, AchievementTypedDict]
    evidence_collection: Dict[str, Any]
    unlocked_stories: List[str]

class SettingsTypedDict(TypedDict, total=False):
    """Schema for user settings."""
    pass 

class RecipeTypedDict(TypedDict, total=False):
    """Schema for crafting recipes."""
    pass

class AudioTypedDict(TypedDict, total=False):
    """Schema for audio and SFX mappings."""
    pass

class LevelAmbianceTypedDict(TypedDict, total=False):
    """Schema for dynamic level ambiance triggers."""
    pass