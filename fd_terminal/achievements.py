# fd_terminal/achievements.py
"""
The Chronicler of Deeds.

This system tracks player achievements, evidence collected, and stories unlocked.
It provides the methods for recording and retrieving player legacy persistently.
"""

import logging
import json
import os
from datetime import datetime
from kivy.app import App
from .resource_manager import ResourceManager

# fd_terminal/achievements.py
"""
The Chronicler of Deeds.

This system tracks player achievements, evidence collected, and stories unlocked.
It provides the methods for recording and retrieving player legacy persistently.
"""

import logging
import json
import os
from datetime import datetime
from kivy.app import App
from .resource_manager import ResourceManager
from .utils import normalize_text  # <-- ADDED: Centralized text normalization

class AchievementsSystem:
    def __init__(self, resource_manager: ResourceManager, notify_callback=None):
        self.logger = logging.getLogger("AchievementsSystem")
        self.resource_manager = resource_manager
        self.notify_callback = notify_callback
        
        # In-memory state
        self.achievements = {}       # Merged state of all achievements
        self.evidence_collection = {} # Dict of evidence_id -> data
        self.unlocked_stories = set() # Set of story_ids
        
        self.logger.info("Chronicler of Deeds initialized.")

    def load_achievements(self):
        """Loads the user profile from disk."""
        path = self._get_save_path()
        
        if not os.path.exists(path):
            self.logger.info("No user profile found. Creating new.")
            self.save_achievements()
            return []

        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                
            # Load raw data into internal state
            self.achievements = data.get("achievements", {})
            self.evidence_collection = data.get("evidence_collection", {})
            self.unlocked_stories = set(data.get("unlocked_stories", []))
            
            self.logger.info(f"Loaded user profile from {path}")
            
            # Retroactive Check
            self._check_for_story_completion(None) 
            
            # FIX: Return a LIST of the values (the achievement dicts), 
            # so the UI can iterate and sort them without crashing.
            return list(self.achievements.values())
            
        except Exception as e:
            self.logger.error(f"Failed to load user profile: {e}", exc_info=True)
            return []

    def _get_save_path(self):
        """Determines the persistent path for user achievements."""
        app = App.get_running_app()
        # Use Kivy's user_data_dir if available, else local 'saves' folder
        if app:
            base_dir = app.user_data_dir
        else:
            base_dir = os.path.join(os.path.dirname(__file__), '..', 'saves')
            
        if not os.path.exists(base_dir):
            os.makedirs(base_dir, exist_ok=True)
            
        return os.path.join(base_dir, "user_profile.json")

    def save_achievements(self):
        """Saves the user profile to disk."""
        path = self._get_save_path()
        
        data = {
            "achievements": self.achievements,
            "evidence_collection": self.evidence_collection,
            "unlocked_stories": list(self.unlocked_stories)
        }
        
        try:
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            self.logger.info(f"User profile saved to {path}")
        except Exception as e:
            self.logger.error(f"Failed to save user profile: {e}", exc_info=True)

    def unlock(self, achievement_id: str) -> bool:
        """Unlocks an achievement if it exists and isn't already unlocked."""
        if achievement_id not in self.achievements:
            return False
        
        ach = self.achievements[achievement_id]
        if ach.get('unlocked'):
            return False # Already unlocked
        
        ach['unlocked'] = True
        ach['unlock_date'] = str(datetime.now())
        self.logger.info(f"Achievement UNLOCKED: {ach.get('name', achievement_id)}")
        
        if self.notify_callback:
            self.notify_callback(f"Achievement Unlocked: {ach.get('name', achievement_id)}")
        
        self.save_achievements()
        return True

    def record_evidence(self, evidence_id: str, name: str, description: str, char_connection: str = None):
        """
        Officially records an item as evidence in the permanent journal.
        Triggers story completion checks.
        """
        if evidence_id in self.evidence_collection:
            return # Already recorded

        entry = {
            "id": evidence_id,
            "name": name,
            "description": description,
            "character_connection": char_connection,
            "found_date": datetime.now().strftime("%Y-%m-%d %H:%M")
        }
        
        self.evidence_collection[evidence_id] = entry
        self.logger.info(f"Evidence recorded: {name}")
        
        # Check if this new piece completes a story
        self._check_for_story_completion(evidence_id)
        self.save_achievements()

    def _check_for_story_completion(self, specific_evidence_id=None):
        """
        Checks evidence_by_source.json to see if ANY story set is now complete.
        """
        evidence_map = self.resource_manager.get_data('evidence_by_source', {})
        
        # --- STEP 1: Build a Robust Lookup Set ---
        collected_lookup = set()
        for evid, data in self.evidence_collection.items():
            # Apply universal normalization to IDs
            collected_lookup.add(normalize_text(evid))
            # Apply universal normalization to Names
            name = data.get('name', '')
            if name:
                collected_lookup.add(normalize_text(name))

        # --- STEP 2: Check Stories ---
        for story_key, story_data in evidence_map.items():
            required_list = story_data.get('evidence_list', [])
            
            # If specific_id passed, ensure it's relevant to this story
            if specific_evidence_id:
                is_relevant = False
                norm_specific = normalize_text(specific_evidence_id)
                for req in required_list:
                    if normalize_text(req) == norm_specific:
                        is_relevant = True
                        break
                if not is_relevant:
                    continue

            # --- STEP 3: The Validation ---
            all_met = True
            for req in required_list:
                # Normalize the requirement from the JSON
                if normalize_text(req) not in collected_lookup:
                    all_met = False
                    break
            
            if all_met:
                if story_key not in self.unlocked_stories:
                    self.unlocked_stories.add(story_key)
                    self.logger.info(f"Story Unlocked: {story_key}")
                    if self.notify_callback:
                        self.notify_callback(f"New Story Unlocked: {story_key}")
                    
                    # Unlock Meta-Achievements
                    self.unlock("lore_master")
                    if len(self.unlocked_stories) >= 5:
                        self.unlock("historian")
                    
                    # Check specific franchise completions
                    if "Final Destination" in story_key:
                         # Simple heuristic: if you have a lot of FD stories, maybe trigger expert
                         pass

    def has_evidence(self, evidence_id: str) -> bool:
        return evidence_id in self.evidence_collection

    def get_all_achievements(self):
        """
        Return the complete list of ALL achievements (locked and unlocked).
        Merges the Master Data (description/icons) with User Data (status).
        """
        try:
            # 1. Get Master List (The Blueprint)
            master_data = self.resource_manager.get_data('player_achievements', {})
            master_list = master_data.get('achievements', {})
            
            final_list = []
            
            # 2. Iterate through EVERY defined achievement
            for ach_id, template in master_list.items():
                # Start with the template (Locked by default)
                entry = template.copy()
                entry['id'] = ach_id
                # Default to locked unless user profile says otherwise
                entry['unlocked'] = False 
                
                # 3. Overlay User Progress
                if ach_id in self.achievements:
                    user_data = self.achievements[ach_id]
                    # Update with user's specific data (unlock date, etc.)
                    entry.update(user_data)
                    
                final_list.append(entry)
                
            return final_list

        except Exception as e:
            self.logger.error(f"Error getting achievements: {e}", exc_info=True)
            return []