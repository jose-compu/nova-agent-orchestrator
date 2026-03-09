# skills-python

Generated Python scripts from Claude Code skills. When you run a skill with a task (e.g. "use the PDF skill to merge two files"), the assistant converts the skill on-the-fly into a minimalistic script, saves it here as `<skill-slug>.py`, and runs it with your task. **Future runs of the same skill reuse the existing script** (same task or a new one passed as argument). You can inspect or run these scripts manually; delete a file to force regeneration next time.
