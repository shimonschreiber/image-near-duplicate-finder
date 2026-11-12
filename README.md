# Image Near-Duplicate Finder

Standalone desktop tool to find **near-duplicate images** using perceptual hashing (pHash).

- No database  
- No config file  
- No permanent cache  

You pick one or more folders; the app scans them in memory, finds similar pairs, and lets you review and delete copies.

## Features

- Add any folders from the UI
- Compute pHash for each image in memory
- Find pairs up to Hamming distance 12 (review filter is adjustable, default 8)
- Sortable list of pairs (distance, paths, dimensions)
- Side-by-side comparison with synchronized zoom/pan
- Highlight the higher-resolution image when sizes differ
- Swap left/right while keeping zoom
- Delete left/right, or move one file into the other’s folder and delete the other
- Multi-select images in the list and bulk-delete
- Keyboard: ← → navigate pairs, Esc close comparison

## Requirements

- Python 3.10+
- Dependencies:

```bash
pip install PySide6 Pillow ImageHash