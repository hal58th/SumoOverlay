# Custom overlay templates

You can customize the two OBS overlays while keeping the app's existing state API, OCR, bout controls, and browser-source setup.

## Copy the starter files

From the project folder, copy whichever overlay templates you want to customize:

- `templates\pre_match_overlay.html` — the pre-match and head-to-head card
- `templates\main_overlay.html` — the always-on lower-third

Place the copies directly in this `custom_templates` folder. Give a copy its own name, for example `my_pre_match.html` or `my_main.html`. Leave `control.html` in `templates`; it is the admin page, not an OBS overlay.

The custom template folder is checked before the built-in `templates` folder, so the built-in template continues to work for any overlay you do not customize.

## Select your files in `config.json`

Set the two `templates` values to the filenames you copied. The names must be plain `.html` filenames in `custom_templates` (no folder paths):

```json
"templates": {
  "main": "my_main.html",
  "pre_match": "my_pre_match.html"
}
```

Keep the complete JSON file valid: retain commas between properties, use double quotes, and do not add a comma after the final property. If you only customize one overlay, keep the other value set to its built-in filename, such as `main_overlay.html`.

Save `config.json`. With the normal settings watcher running, the selection takes effect without restarting the app. Refresh the OBS Browser Source if it does not display the new template immediately. Continue using the same OBS URLs:

- Main overlay: `http://127.0.0.1:5000/main-overlay`
- Pre-match overlay: `http://127.0.0.1:5000/overlay`

## Keep the app integration

The starter HTML files are the safest starting point. They use `/api/state` or `/api/pre-match-state` and listen for the app's Socket.IO updates. If you replace the markup or JavaScript, keep the endpoint/event handling your page needs for live updates. Keep overlay position and canvas handling if you want the `overlay.main` and `overlay.pre_match` settings to keep positioning your content inside OBS's configured canvas.

You can freely edit the HTML, CSS, and JavaScript in the copied files. The shared stylesheet is `static\overlay.css`; custom templates can continue referencing it with Flask's `url_for('static', filename='overlay.css')`. For CSS changes that should not affect the built-in overlays, add a separate stylesheet under `static` and reference it from your custom template.
