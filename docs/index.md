# Hermes Chrome

Local agent companion so Hermes / AI agents can operate **your Chrome** on **any website**
without hijacking the tab you are using.

**Two parts required:** machine **companion** (bridge + Native Messaging host from the
repo) + browser **extension** (Chrome Web Store or Load unpacked). Extension alone
cannot open a control port.

- **Repository:** [github.com/leaf76/hermes-chrome](https://github.com/leaf76/hermes-chrome)
- **User guide (EN):** [GUIDE.md](./GUIDE.md)
- **使用教學（繁中）:** [GUIDE.zh-TW.md](./GUIDE.zh-TW.md)
- **Privacy policy:** [privacy-policy.md](./privacy-policy.md)
- **Chrome Web Store package:** see repo `store/` after `./store/package.sh`
- **In-extension guide:** popup → **Guide** (`help.html`)
- **Packaging / release:** [PACKAGING.md](./PACKAGING.md)

Default agent workspace uses a Chrome Tab Group. Open any `http(s)` URL via CLI/MCP;
optional product finders are helpers only, not hard-coded limits.
