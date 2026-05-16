# fd_terminal/vfx_manager.py
import logging
from kivy.uix.widget import Widget
from kivy.uix.image import Image
from kivy.graphics import Color, Rectangle
from kivy.animation import Animation
from kivy.metrics import dp
from kivy.clock import Clock

class VFXManager:
    """
    The Director of Cinematography.
    Handles all visual overlays, screen flashes, and flying objects without cluttering the UI.
    """
    def __init__(self, game_screen, audio_manager=None):
        self.logger = logging.getLogger(__name__ + ".VFXManager")
        self.screen = game_screen
        self.audio = audio_manager

    def play_vfx(self, vfx_tag: str, on_complete: callable):
        """Routes the requested VFX tag to its specific animation sequence."""
        self.logger.info(f"Playing VFX sequence: {vfx_tag}")
        
        # Lock player input during the movie
        self.screen.disabled = True 

        if vfx_tag == "flying_tank":
            self._vfx_flying_tank(on_complete)
        elif vfx_tag == "qte_blood_splatter":
            self._vfx_blood_splatter(on_complete)
        else:
            self.logger.warning(f"Unknown VFX tag '{vfx_tag}'. Skipping to next screen.")
            self.screen.disabled = False
            on_complete()

    # ---------------------------------------------------------
    # VFX SEQUENCES
    # ---------------------------------------------------------

    def _vfx_flying_tank(self, on_complete):
        # 1. Play the sickening sound
        if self.audio:
            # Replace with your actual sound key if you have one
            self.audio.play_sfx('squelch_bone_crunch') 

        # 2. Setup the flying object (Make sure you have an image at this path!)
        # If you don't have an image yet, this will just show a white box, which is fine for testing.
        tank_img = Image(
            source='assets/images/vfx/oxygen_tank.png', 
            size_hint=(None, None),
            size=(dp(200), dp(80)),
            pos_hint={'center_x': -0.5, 'center_y': 0.5} # Start way off-screen left
        )
        self.screen.add_widget(tank_img)

        # 3. Setup the screen flash overlay (Starts invisible)
        overlay = Widget(size_hint=(1, 1))
        with overlay.canvas.after: # Draw over absolutely everything
            self.flash_color = Color(1, 0, 0, 0) # Red, 0 Opacity
            self.flash_rect = Rectangle(pos=self.screen.pos, size=self.screen.size)
        
        # Keep overlay sized correctly if window resizes
        overlay.bind(pos=lambda _, v: setattr(self.flash_rect, 'pos', v),
                     size=lambda _, v: setattr(self.flash_rect, 'size', v))
        self.screen.add_widget(overlay)

        # 4. Choreograph the Animation
        # A: Tank flies to the center FAST (0.15s)
        anim_fly = Animation(pos_hint={'center_x': 0.5}, duration=0.15, transition='in_circ')
        
        # B: Instant red flash, then fade to black (1.0s)
        def trigger_flash(animation, widget):
            anim_flash = Animation(a=1, r=1, g=0, b=0, duration=0.05) + \
                         Animation(a=1, r=0, g=0, b=0, duration=1.0)
                         
            def cleanup(*args):
                self.screen.remove_widget(tank_img)
                self.screen.remove_widget(overlay)
                self.screen.disabled = False
                on_complete()
                
            anim_flash.bind(on_complete=cleanup)
            anim_flash.start(self.flash_color)

        anim_fly.bind(on_complete=trigger_flash)
        anim_fly.start(tank_img)

    def _vfx_blood_splatter(self, on_complete):
        """A simple example of a secondary effect."""
        overlay = Widget(size_hint=(1, 1))
        with overlay.canvas.after:
            self.flash_color = Color(0.8, 0, 0, 0)
            self.flash_rect = Rectangle(pos=self.screen.pos, size=self.screen.size)
            
        overlay.bind(pos=lambda _, v: setattr(self.flash_rect, 'pos', v),
                     size=lambda _, v: setattr(self.flash_rect, 'size', v))
        self.screen.add_widget(overlay)

        # Flash red quickly, then fade out
        anim = Animation(a=0.6, duration=0.1) + Animation(a=0, duration=0.5)
        
        def cleanup(*args):
            self.screen.remove_widget(overlay)
            self.screen.disabled = False
            on_complete()
            
        anim.bind(on_complete=cleanup)
        anim.start(self.flash_color)