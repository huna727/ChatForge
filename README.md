# AI Chat → Project File Manager

A small desktop app that turns a pasted AI conversation into real files and folders on disk.

Ask an AI (Claude, ChatGPT, whatever) to write or fix code for you, using the simple syntax below. Paste the whole conversation into this app, hit **Analyze**, review what it found, and hit **Save Files** — it writes everything out with the correct names, extensions, and folder structure. No copy-pasting code blocks into files by hand.

- Pure Python standard library — no dependencies to install, no build step.
- Multiple projects at once, each in its own tab.
- Point it at an existing project folder and it loads what's already there.
- Understands new files, nested folders, and patches (full-file replacements) to existing files.
- Built-in system prompt generator so your AI knows how to format its output for this tool.

## Requirements

- Python 3.8+
- `tkinter` (bundled with Python on Windows and macOS; on Linux you may need `sudo apt install python3-tk` or your distro's equivalent)

That's it — no `pip install` needed for the core app.

## Running it

```bash
python ai_chat_to_files.py
```

## How it works

1. **Choose Output Folder** — pick an empty folder for a new project, or an existing project folder to load it. The app scans it in the background and shows you what's already there.
2. **Paste** an AI conversation into the text box (or drag and drop a `.txt` file, if drag-and-drop support is available on your system).
3. Click **Analyze Conversation**. It scans the pasted text for the file syntax below, adds everything it finds to the project's pending file tree, and clears the box so you can paste the next message in the same conversation.
4. Click **Save Files** when you're ready. Files are written to disk, and the folder opens automatically.

You can paste and analyze multiple conversations into the same project before saving — later patches to the same file simply override earlier ones.

## File syntax

Tell your AI to format its output like this (or just copy the built-in **AI System Prompt** from the app's header into your AI's system prompt / first message).

### A new file

````
>>filename<</.extension/
^start^
...file contents...
^end^
````

Example:

````
>>main<</.py/
^start^
print("Hello")
^end^
````

Creates `main.py`.

### A new file inside folders

Prefix with one `#` per folder level:

````
#src#utils#>>helper<</.py/
^start^
def helper():
    return 1
^end^
````

Creates `src/utils/helper.py`.

### Patching an existing file

Put a single `$` immediately before the marker. This replaces the contents of an **existing** file instead of creating a new one:

````
$>>main<</.py/
^start^
print("Updated")
^end^

$#src#utils#>>helper<</.py/
^start^
def helper():
    return 2
^end^
````

The code between `^start^` and `^end^` for a patch is the file's complete new contents (a full replacement, not a diff).

### Project root name (optional)

Anywhere in the response, outside a code block, you can include:

```
%MyProjectName%
```

This is informational — a suggested name for the project.

## Extension handling

The filename and extension are parsed as two separate pieces of information and combined exactly once, so you'll never end up with `config.json.json` or `main.py.py` — even if a markdown code-fence language tag (like ` ```json `) appears right next to the marker.

## Safety

- Filenames and folder paths are sanitized: no `../` traversal, no absolute paths, no drive letters.
- Every resolved file path is checked to make sure it stays inside the chosen output folder before anything is written.
- Nothing touches disk until you click **Save Files**.

## Project structure

Everything lives in a single file, `ai_chat_to_files.py`:

- **Parser core** — pure functions (`parse_conversation`, `group_changes`, `build_relative_path`, etc.) with no UI dependencies, so the file-detection logic can be tested independently of the GUI.
- **GUI** — built with Tkinter, styled to not look like a default OS dialog.

## Contributing

Issues and pull requests welcome. If you're changing the parsing logic, please add a case to the test scenarios covering:

- New files, nested folders, and patches
- Duplicate filenames within one conversation
- Markdown fences around a file block
- Windows (`\r\n`) and Unix (`\n`) line endings
- Missing `^start^` / `^end^` markers
- Path traversal attempts (`../`)

## License

Add your preferred license here (e.g. MIT).
