# MeCoffee Service Bot

Telegram bot for location-based maintenance reports and stock receipt logging.

## Required environment variables

- \`BOT_TOKEN\` — Telegram bot token.
- \`GOOGLE_CREDENTIALS\` — service-account credentials JSON, as a single-line JSON environment value. Share the target Google Sheet with the service-account email.
- \`SPREADSHEET_NAME\` — optional; defaults to \`Mecoffee_Service_Reports\`.
- \`LOCATION_ACCOUNTS_JSON\` — credentials and worksheet mapping for each location.
- \`MAINTENANCE_GUIDES_JSON\` — optional guide URLs, keyed by maintenance step.

### Location accounts

Set \`LOCATION_ACCOUNTS_JSON\` in the hosting provider's secret/environment settings. Do not commit real passwords to GitHub.

Example with **dummy passwords only**:

\`\`\`json
{
  "rakovnik": {
    "login": "rakovnik",
    "password": "CHANGE_THIS",
    "location": "Rakovník – fitness",
    "worksheet": "Rakovnik-fitnes"
  },
  "brod": {
    "login": "brod",
    "password": "CHANGE_THIS",
    "location": "Železný Brod – Pizza Padre",
    "worksheet": "Zelezny-brod-pizza"
  },
  "hradek": {
    "login": "hradek",
    "password": "CHANGE_THIS",
    "location": "Hrádek u Rokycan",
    "worksheet": "Hradek-u-Rokycan"
  }
}
\`\`\`

Only configure locations and worksheet names that should be active. The Google spreadsheet must have access for the service account. If a maintenance worksheet does not exist, the bot creates it with headers.

The location account is shared by staff at that point. Reports identify the location account and Telegram display/username; they cannot reliably identify the individual staff member unless staff identification is added separately.

### Maintenance guide URLs

All links are optional until the real guides are ready. Use direct HTTPS links to PDFs, videos, or instruction pages. Configure them in the hosting provider, not in code.

\`\`\`json
{
  "waste": "https://example.com/waste-instructions.pdf",
  "drip_tray": "https://example.com/drip-tray-video",
  "exterior": "https://example.com/exterior-cleaning",
  "coffee_unit": "https://example.com/coffee-machine-manual"
}
\`\`\`

Replace example URLs with the actual MeCoffee instructions. The keys must match the step keys above.

## Bot behavior

- On \`/start\`, staff log in with the shared credentials for their location.
- After login, the main menu contains only \`🧽 Údržba\` and \`📦 Příjem zboží\`.
- Maintenance guides the user step by step, allows an optional guide link per step, records issues, and collects before/after photos and ingredient quantities.
- Goods receipt accepts multiple products, quantities, and units (\`ks\`, \`balení\`, \`g\`, \`kg\`, \`ml\`, \`l\`) and writes rows to the \`Příjem zboží\` worksheet.
- Sessions are held in memory, so staff must log in again after a server restart or redeploy.
- Staff can use \`/login\` to switch accounts and \`🚪 Odhlásit\` to log out.

## Deployment notes

1. Set the environment variables above in the existing hosting service.
2. Confirm the configured worksheet names match the actual Google Sheets tabs.
3. Set the Telegram webhook to the existing public webhook URL ending in \`/<BOT_TOKEN>\`.
4. Test login, maintenance, issue reporting, and a multi-product receipt before using it operationally.
5. Configure actual guide URLs before expecting the guide buttons to open instructions.
