# main.py
import os
import sys
import logging
import random
from kivy.utils import platform

# This is the most critical step. It tells Python that the 'fd_terminal' folder
# is a place where it can find modules to import.
# We add the current directory (where 'main.py' and 'fd_terminal' live) to the path.
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

def setup_initial_logging():
    """Sets up a basic logger before the Kivy app takes over."""
    handlers = []
    # Always log to logcat/console
    handlers.append(logging.StreamHandler())
    # On Android, also log to a file
    if platform == "android":
        try:
            from jnius import autoclass
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            activity = PythonActivity.mActivity
            if activity:
                ext_dir = activity.getExternalFilesDir(None)
                if ext_dir:
                    safe_log_dir = str(ext_dir.getAbsolutePath())
                    os.makedirs(safe_log_dir, exist_ok=True)
                    handlers.append(logging.FileHandler(os.path.join(safe_log_dir, "fdt_boot_log.txt")))
        except Exception as e:
            print(f"Could not create file handler for logging: {e}")

    logging.basicConfig(
        level=logging.INFO,
        format='FDTAPP %(asctime)s - %(levelname)s - %(name)s - %(message)s',
        handlers=handlers,
        encoding='utf-8'
    )
    print("FDTAPP: About to initialize logger")
    logging.info("Launcher: Initializing...")
    print("FDTAPP: Logger initialized")

def main():
    """The main entry point for the application."""
    setup_initial_logging()
    
    # The pool of punchy, horror-themed engine startup lines
    startup_quotes = [
        "Turning the ignition on the DieNamic Engine.",
        "The DieNamic Engine roars to life\nHungry for a fresh batch of digital souls...",
        "DieNamic Engine online.\nAwaiting a fresh set of idiot teenagers with big life plans.",
        "Initializing the DieNamic Engine.\nGood luck, you'll need it.",
        "Feeding fresh variables into the DieNamic Engine's meat grinder.",
        "Fasten your seatbelts and raise your tray tables.\nDieNamic Engine is ready for takeoff.",
        "Spooling up the DieNamic Engine.\nYou just be careful now.",
        "DieNamic Engine initialized.\nNo accidents, no mishaps, and no escapes."
    ]
    
    try:
        # Now that the path is set, we can perform a non-relative import.
        # We are telling Python to "from the fd_terminal package, import the main scroll."
        from fd_terminal.main import FinalDestinationApp
        
        # Pick a random quote from the pool
        selected_quote = random.choice(startup_quotes)
        logging.info(f"Launcher: {selected_quote}")
        
        FinalDestinationApp().run()
        
    except ImportError as e:
        logging.critical(f"FATAL LAUNCH ERROR: Could not import the application. Check that 'fd_terminal' is a valid package.", exc_info=True)
        sys.exit(1)
    except Exception as e:
        logging.critical(f"An unexpected fatal error occurred during launch.", exc_info=True)
        sys.exit(1)

# This is the command to start everything when you run this file.
if __name__ == "__main__":
    main()