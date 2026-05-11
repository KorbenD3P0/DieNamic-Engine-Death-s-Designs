import shutil
import os
import datetime
import logging

def create_backup():
    # Setup logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger("BackupSystem")

    # Define paths
    project_root = os.path.dirname(os.path.abspath(__file__))
    backup_dir = os.path.join(project_root, "backups")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{timestamp}"
    backup_path = os.path.join(backup_dir, backup_name)

    # Dirs to backup
    dirs_to_backup = ["fd_terminal", "data", "assets"]
    files_to_backup = ["main.py", "requirements.txt", "buildozer.spec"]

    try:
        os.makedirs(backup_path, exist_ok=True)
        logger.info(f"Created backup directory: {backup_path}")

        for d in dirs_to_backup:
            src = os.path.join(project_root, d)
            dst = os.path.join(backup_path, d)
            if os.path.exists(src):
                shutil.copytree(src, dst)
                logger.info(f"Backed up directory: {d}")
            else:
                logger.warning(f"Directory not found: {d}")

        for f in files_to_backup:
            src = os.path.join(project_root, f)
            dst = os.path.join(backup_path, f)
            if os.path.exists(src):
                shutil.copy2(src, dst)
                logger.info(f"Backed up file: {f}")
            else:
                logger.warning(f"File not found: {f}")

        # Create an archive for easier handling
        shutil.make_archive(backup_path, 'zip', backup_path)
        logger.info(f"Backup archived to {backup_path}.zip")
        
        # Cleanup uncompressed folder if desired, but keeping it for now is safer/faster access
        # shutil.rmtree(backup_path) 
        
        print(f"SUCCESS: Backup completed at {backup_path}.zip")

    except Exception as e:
        logger.error(f"Backup failed: {e}")
        print(f"FAILURE: Backup failed. Check logs.")

if __name__ == "__main__":
    create_backup()
