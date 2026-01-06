# DSTS Unified - Digimon Time Stranger Blender Tools

A Blender addon for importing and exporting `.geom` (3D models) and `.anim` (animations) files from **Digimon Story: Cyber Sleuth** and related games.

![Blender](https://img.shields.io/badge/Blender-4.5+-orange?logo=blender)
![License](https://img.shields.io/badge/License-MIT-blue)

## Features

### Model Import/Export (.geom)
- Full skeleton/armature support
- Meshes with materials and textures
- Vertex attributes: positions, normals, UVs (up to 3 layers), vertex colors, tangents
- Bone weights (up to 4 influences per vertex)
- Custom image folder selection during import
- **Import textures**: Supports `.img`, `.dds`, and `.png` formats
- **Export textures**: As `.img` (DDS) or `.png`

### Animation Import/Export (.anim)
- Import single or multiple animations
- Auto-import animations matching the geom filename
- Export single NLA track or batch export all tracks
- Rotation, position, and scale keyframes

### Quality of Life
- **Export All**: One-click export of geom, textures, and all animations
- Custom shader node system for material data preservation
- Automatic mesh triangulation for tangent calculation
- Smart vertex group limiting (keeps top 4 bone influences)

## Installation

1. Download the latest release or clone this repository
2. In Blender, go to **Edit → Preferences → Add-ons**
3. Click **Install** and select the addon folder or zip file
4. Enable **"DSTS Unified (Digimon Time Stranger)"**

## Usage

### Importing

**File → Import → DSTS**

| Option | Description |
|--------|-------------|
| **DSTS Geom** | Import a `.geom` model file |
| **DSTS Animation** | Import `.anim` file(s) to an existing armature |

#### Geom Import Options
- **Custom Images Folder**: Enable to select a custom folder for textures instead of the default `<geom_folder>/images/`

### Exporting

**File → Export → DSTS**

| Option | Description |
|--------|-------------|
| **DSTS Export All** | Export geom + nlst + textures + all animations |
| **DSTS Geom Only** | Export only the `.geom` file |
| **DSTS Animation (Single)** | Export a single NLA track |
| **DSTS Animations (All)** | Batch export all NLA tracks |

#### Export Options
- **Collection**: Select which collection to export (must have been imported with this addon)
- **Export Textures**: Enable/disable texture export
- **Export as PNG**: Export textures as `.png` instead of `.img` (DDS)

## File Structure

When exporting, the addon creates:
```
output_folder/
├── model.geom          # 3D model data
├── model.nlst          # Name list file
├── images/
│   ├── texture1.img    # Textures (or .png if selected)
│   └── texture2.img
├── animation1.anim     # Animation files
└── animation2.anim
```

## Requirements

- **Blender 4.5+**
- Python packages: `numpy` (included with Blender)

## Known Limitations

- Maximum 4 bone influences per vertex (additional influences are automatically reduced to top 4 by weight)
- Meshes with n-gons are automatically triangulated for tangent calculation

## Credits

- **Nymic_Razor** - Original geom format research
- **Pherakki** - Animation format research
- **Romstarr** - Base DSTS format 
- Combined and maintained by the community

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request
