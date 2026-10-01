# Branding

Name, logo, colours and footer links are configuration. None of them is in the repository, so an update never overwrites them.

## Names

| Variable | Used for |
|---|---|
| `LABCIRS_ORGANIZATION` | The word mark in the top bar when there is no logo, the alternative text of the logo, the footer and the page titles. |
| `LABCIRS_SITE_NAME` | The name of the system. It is shown next to the logo, in page titles ("Page · name"), in the admin and in the footer. Default `LabCIRS`. |

If you set no logo and both names are the same, the top bar shows the name once.

## The `/branding/` folder

The proxy serves the directory named by `BRANDING_DIR` (default `./branding` next to `compose.yaml`) under `/branding/`, read-only. Put the logo and the theme stylesheet there. The directory is in `.gitignore`.

```
branding/
  logo.svg
  theme.css
```

Then set in `.env`:

```
LABCIRS_LOGO_URL=/branding/logo.svg
LABCIRS_THEME_CSS_URL=/branding/theme.css
```

and restart the app: `docker compose up -d`. Files in `branding/` need no restart, only the two settings do.

Both URLs must point to the same host as the site. The Content Security Policy allows images and stylesheets from the site itself only (`default-src 'self'; img-src 'self' data:`). A logo on another host is blocked by the browser, and so is a web font from a font service.

## Logo

- SVG or PNG. SVG stays sharp at every size.
- The top bar is white. The logo is shown 32 px high (24 px on narrow screens) at its natural width. A wide logo leaves less room for the navigation: logo, navigation and language switch share the bar and wrap to a second line on narrow screens, so look at the result with all languages switched on.
- It is also shown in the admin, at 32 px high.
- The text for screen readers is `LABCIRS_ORGANIZATION`. Do not rely on text inside the image.

Without `LABCIRS_LOGO_URL` the top bar shows the organization name as text.

## Colours

`theme.css` is loaded after the built-in stylesheets and overrides the colour tokens defined on `:root` in `static/css/core.css`. The brand colour is five tokens:

| Token | Used for |
|---|---|
| `--ui-marke` | Buttons, links, headings, the footer background. Text on white, and white text on it. |
| `--ui-marke-dunkel` | Hover of buttons and links. White text on it. |
| `--ui-marke-hell` | Light brand surface: selected rows, initials, badges. Dark text on it. |
| `--ui-marke-rand` | Border on that light surface. |
| `--ui-marke-akzent` | The brand colour on a dark background: focus ring on the footer. |

Example, a green brand:

```css
:root {
  --ui-marke: #1d6b3a;
  --ui-marke-dunkel: #124a28;
  --ui-marke-hell: #e8f3ec;
  --ui-marke-rand: #b5d3c0;
  --ui-marke-akzent: #9fd8b4;
}
```

One colour serves as text and as background, so check the contrast before you ship. White on `--ui-marke` and `--ui-marke` on white must both reach 4.5:1 (WCAG AA), `--ui-marke` on `--ui-marke-hell` as well. The example gives 6.5:1, 10.3:1 for white on `--ui-marke-dunkel` and 5.7:1 on the light surface.

Only the public pages load `theme.css`. The admin has its own stylesheet, `static/css/admin-theme.css`, with the default blue written out as plain values (the admin cannot see the tokens). To recolour the admin as well, edit those values and rebuild the image.

The other tokens (greys, status colours, spacing, type) can be overridden the same way. Leave the status colours alone unless you have to: they are chosen for contrast and for being told apart.

The pages are always light. They do not follow the dark mode of the browser.

## Footer links

| Variable | Result |
|---|---|
| `LABCIRS_IMPRINT_URL` | Link "Imprint". Shown only when set. |
| `LABCIRS_PRIVACY_URL` | Link "Privacy". Shown only when set. |
| `LABCIRS_SOURCE_URL` | Link "<site name> · based on LabCIRS (AGPL)". |

LabCIRS is licensed under the AGPL. Users of a modified version must be able to get its source code. Keep the source link and point it at your copy if you changed the code. The footer also shows the version (`Version 8.0.0a1`).
