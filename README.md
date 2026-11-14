# PS4 Toolkit

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue?logo=python&logoColor=white)
![Platform: Windows](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)
![Status: stable](https://img.shields.io/badge/features/status-stable-brightgreen)
![Notifications](https://img.shields.io/badge/feature-notifications-blue)
![Payloads](https://img.shields.io/badge/feature-payload%20sender-purple)
![Payload library](https://img.shields.io/badge/feature-payload%20library-purple)
![Live klog](https://img.shields.io/badge/feature-live%20klog-green)
![Port check](https://img.shields.io/badge/feature-port%20check-teal)
![FTP](https://img.shields.io/badge/feature-FTP%20launcher-teal)
![Reboot](https://img.shields.io/badge/feature-reboot-red)

A small local tool for talking to a jailbroken PS4 from your PC. It runs a
Python server on your own machine and opens a control panel in your browser.

It can:

- Send on-screen notifications to the console
- Send payloads (`.bin` / `.elf`) and keep them in a library
- Show live klog output from the console
- Check which service ports are open
- Open an FTP connection to the console
- Reboot the console

> Only use this on a console you own, and only with payloads you trust.

## Requirements

- Windows (the launcher is a `.bat` file; the Python code also runs on other systems)
- Python 3.8 or newer, from python.org. Tick **Add Python to PATH** during install.
- A PS4 on the same network as your PC, with its debug service running

No extra Python packages are needed. The toolkit uses only the standard library.

## Getting started

1. Unzip the folder anywhere you like.
2. Double-click **Start PS4 Toolkit.bat**. The control panel opens in your browser.
3. Type your PS4's IP address into the **PS4 IP address** box and press **Enter**.
   The status pill turns green when the console answers.

To start it without the launcher, run `python ps4_toolkit.py`. Add `--app` to open
an Edge or Chrome app window, or `--no-browser` to just start the server and print
the address.

Closing the browser tab, or pressing the power button in the top corner, shuts the
toolkit down.

## Using it

### Notify

Type a message and press **Send notification** (or **Ctrl+Enter**). The message is
sent to port 744.

- **Templates** are saved messages you can reuse. Click **Save current** to save the
  text in the box. Click the × on a template to delete it.
- **Recent messages** lists your last 10 sent messages. Click one to load it again.

### Payload

Drop a `.bin` or `.elf` file onto the drop zone, or click it to browse. Files are
copied into your library so you can resend them later.

- Pick a payload, choose a port (default `9090`), and press **Send payload**.
- Tick **Send a notification when the payload is sent** to get a popup on the console.
- Click the trash icon on a library item to remove it.

### Klog

Press **Start** to connect to the console's klog service (default port `3232`) and
watch its output live. Use **Filter** to narrow the lines, **Save** to write them to a
file, and **Clear** to empty the view.

### Tools

- **Check ports** briefly connects to each service port. Nothing is sent.
  Payload ports (`9020`, `9021`, `9090`) are skipped unless you tick
  **Include payload ports**.
- **Open FTP** opens `ftp://<console IP>:2121` with your default FTP handler, usually Windows Explorer.
- **Copy IP** copies the address to the clipboard.
- **Reboot PS4** asks for confirmation, then sends a reboot command on port 744.

When an address first answers, the ports are checked once automatically.

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+1 to 4 | Switch to Notify, Payload, Klog or Tools |
| Ctrl+Enter | Send the notification |
| Enter (in the IP box) | Save the IP address and check it |

## Ports used

| Port | Service |
| --- | --- |
| 744 | Notifications and reboot |
| 2121 | FTP |
| 3232 | Klog |
| 9090 | Payload sending (default) |
| 9020, 9021 | Other payload ports (checked only on request) |

## Settings and data

Your settings and payload library are kept in:

- `%APPDATA%\PS4Toolkit\settings.json` (or `~\PS4Toolkit\` if `APPDATA` is not set)
- `%APPDATA%\PS4Toolkit\payloads\` for the payload library

The IP address you type is not kept between restarts. Recent addresses are.
To forget one address, click the × on it. To forget them all, press **Clear recent**.

## Changing the icon

Put your own `icon.png` (or `.svg`, `.ico`, `.webp`, `.jpg`) next to
`ps4_toolkit.py` to replace the header logo and the browser tab icon. Files
up to 2 MB are supported. Without one, a built-in blue icon is used.

## Files

| File | What it is |
| --- | --- |
| `Start PS4 Toolkit.bat` | Starts the toolkit without a console window |
| `ps4_toolkit.py` | The Python server and the console protocol code |
| `index.html` | The control panel layout |
| `style.css` | Styling |
| `app.js` | Browser-side behaviour |
| `icon.png` | Optional custom icon |

## Troubleshooting

- **Status says "No reply":** check the IP, that the PC and console are on the same
  network, and that the console's debug service is running.
- **Reboot does not behave as expected:** the reboot command is not yet verified
  against your console's firmware. Test it before relying on it.
- **Nothing opens in the browser:** run `python ps4_toolkit.py --no-browser` and open
  the address it prints.

## Disclaimer

This is an unofficial tool. PlayStation and PS4 are trademarks of Sony Interactive
Entertainment. This project is not made by or endorsed by Sony.


