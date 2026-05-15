# fd_terminal/audio_manager.py
import logging
import random
from kivy.core.audio import SoundLoader
from kivy.clock import Clock
from .resource_manager import ResourceManager

class AudioManager:
    """
    The Voice of the Design.
    Manages loading, playing, and fading of auditory hallucinations.
    """
    def __init__(self, resource_manager: ResourceManager):
        self.logger = logging.getLogger("AudioManager")
        self.resource_manager = resource_manager
        self.active_sounds = {}
        self.master_volume = 0.8  # Default value, will be overwritten by App later
        self.current_music = None
        self.current_track_key = None
        self._fade_event = None  # Track the active fade timer
        self.audio_data = self.resource_manager.get_data('audio', {})
        self.logger.info("AudioManager initialized.")

    def set_master_volume(self, volume: float):
        """Updates master volume and adjusts currently playing music immediately."""
        self.master_volume = max(0.0, min(1.0, volume))
        
        # Immediate update for music
        if self.current_music and self.current_music.state == 'play':
            # We need to know the track's specific volume to scale it
            if self.current_track_key:
                music_data = self.audio_data.get('music', {}).get(self.current_track_key)
                track_vol = music_data.get('volume', 0.5)
                self.current_music.volume = track_vol * self.master_volume

    def play_sfx(self, key: str):
        """Plays a 'fire-and-forget' sound effect."""
        sfx_data = self.audio_data.get('sfx', {})
        if key not in sfx_data:
            self.logger.warning(f"[AudioManager] Sound key '{key}' not found.")
            return

        sound_info = sfx_data[key]
        file_path = sound_info.get('file')
        volume = sound_info.get('volume', 1.0) * self.master_volume
        pitch = random.uniform(0.95, 1.05)

        try:
            sound = SoundLoader.load(file_path)
            if sound:
                sound.volume = volume
                sound.pitch = pitch
                sound.play()
                self.logger.info(f"[AudioManager] Playing SFX: {key}")
            else:
                self.logger.error(f"[AudioManager] Failed to load: {file_path}")
        except Exception as e:
            self.logger.error(f"[AudioManager] Error playing '{key}': {e}")

    def play_music(self, key: str, fallback: str = None):
        """
        Plays a looping track. 
        Smart Logic: If the requested track is currently fading out, it cancels the fade
        and restores volume instead of restarting.
        """
        # 1. Load target volume for this track
        music_data = self.audio_data.get('music', {}).get(key)
        if not music_data:
            if fallback:
                self.logger.info(f"[AudioManager] Music key '{key}' not found. Trying fallback '{fallback}'.")
                return self.play_music(fallback)
            else:
                self.logger.warning(f"[AudioManager] Music key '{key}' not found.")
                return
        target_volume = music_data.get('volume', 0.5) * self.master_volume

        # 2. Check continuity
        if self.current_track_key == key and self.current_music:
            # If currently fading out, rescue it!
            if self._fade_event:
                self._fade_event.cancel()
                self._fade_event = None
                self.current_music.volume = target_volume
                self.logger.info(f"[AudioManager] Fade cancelled. Resuming '{key}' at full volume.")
                return
            
            # If already playing normally, do nothing
            if self.current_music.state == 'play':
                return

        # 3. Stop previous music (Instant cut if switching tracks)
        self.stop_music(fade=False)

        file_path = music_data.get('file')
        loop = music_data.get('loop', True)

        try:
            sound = SoundLoader.load(file_path)
            if sound:
                sound.loop = loop
                sound.volume = target_volume
                sound.play()
                self.current_music = sound
                self.current_track_key = key
                self.logger.info(f"[AudioManager] Music started: {key}")
            else:
                self.logger.error(f"[AudioManager] Failed to load music: {file_path}")
        except Exception as e:
            self.logger.error(f"[AudioManager] Exception playing music '{key}': {e}")

    def stop_music(self, fade=False, duration=1.5):
        """
        Stops the music. 
        If fade=True, lowers volume over 'duration' seconds before stopping.
        """
        if not self.current_music:
            return

        # Cancel any existing fade to prevent conflict
        if self._fade_event:
            self._fade_event.cancel()
            self._fade_event = None

        if fade:
            self.logger.info(f"[AudioManager] Fading out music ({duration}s)...")
            start_vol = self.current_music.volume
            # 30 updates per second is smooth enough
            steps = int(duration * 30)
            if steps < 1: steps = 1
            vol_step = start_vol / steps

            def _fade_step(dt):
                if not self.current_music:
                    return False
                
                new_vol = self.current_music.volume - vol_step
                if new_vol <= 0:
                    # Fade complete
                    self.current_music.stop()
                    self.current_music = None
                    self.current_track_key = None
                    self._fade_event = None
                    self.logger.info("[AudioManager] Fade complete. Music stopped.")
                    return False # Stop scheduling
                
                self.current_music.volume = new_vol
                return True # Continue scheduling

            self._fade_event = Clock.schedule_interval(_fade_step, 1.0/30.0)
        
        else:
            # Instant Stop
            try:
                self.current_music.stop()
                self.logger.info(f"[AudioManager] Music stopped immediately.")
            except Exception as e:
                self.logger.warning(f"[AudioManager] Error stopping music: {e}")
            self.current_music = None
            self.current_track_key = None