import os
import shutil
from PIL import Image

# Paths and configuration
base_path = r"E:\Private\#Private#\restoredpics"
low_folder = os.path.join(base_path, "low")
high_folder = os.path.join(base_path, "high")

# Create "low" and "high" directories if they don't exist
os.makedirs(low_folder, exist_ok=True)
os.makedirs(high_folder, exist_ok=True)

# function to sort images
def sort_images_by_dimensions():
    # Loop through each folder from 1 to 108
    for folder_index in range(1, 109):
        folder_path = os.path.join(base_path, f"folder_{folder_index}")
        # Check if folder exists (if not) print "folder not found" and continue
        if not os.path.exists(folder_path):
            print(f"Folder not found: {folder_path}")
            continue
        # Loop through each file in the folder
        for filename in os.listdir(folder_path):
            file_path = os.path.join(folder_path, filename)

            # Skip non-image files
            if not (filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp', '.gif'))):
                continue

            try:
                # Open the image to get dimensions
                with Image.open(file_path) as img:
                    width, height = img.size

                # Determine the destination folder
                if width < 640 and height < 640:
                    dest_folder = low_folder
                else:
                    dest_folder = high_folder

                # Move the image
                shutil.move(file_path, os.path.join(dest_folder, filename))
                print(f"Moved: {file_path} -> {dest_folder}")

            except Exception as e:
                print(f"Error processing {file_path}: {e}")

# Run the function
if __name__ == "__main__":
    sort_images_by_dimensions()
    print("Sorting complete.")
