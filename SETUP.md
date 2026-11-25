# UO class seat monitor: setup

This is set up just like the lookout monitor: GitHub Actions checks the public UO
class schedule about every 10 minutes and pushes to your phone through ntfy. You then
register yourself. It never logs in.

**What triggers an alert:** a STAT 243Z lecture *and* one of its linked discussions
both have a seat open, meaning a pair you can actually register for. The alert
lists both CRNs.

---

### Step 1: Make the repo
1. Unzip `class-monitor.zip` somewhere outside Dropbox (for example `~/class-monitor`).
2. In GitHub Desktop: **File → Add Local Repository** → choose the folder → when it
   asks, click **create a repository** → then **Publish repository**. Leave
   **Keep this code private** checked.

### Step 2: Add your ntfy topic as a secret
On github.com, open the new repo → **Settings → Secrets and variables → Actions →
New repository secret**
- Name: `NTFY_TOPIC`
- Value: the same topic your lookout monitor uses (that's simplest, since your phone is
  already subscribed), or a new one.

### Step 3: Do a test run (important)
1. Go to the **Actions** tab. If asked, click **I understand… enable workflows**.
2. Click **Check class seats → Run workflow**.
3. When it finishes, open the run → **Run monitor** step. You should see lines like:
   ```
   STAT 243Z Lecture 24919 0/493 tr 1200-1320 Henderson K
       + Dis    24920 0/29 r 1500-1550, f 0900-0950
   ```
   If you see those lines, it's working. If you see an `ERROR` instead, copy the
   log into the chat and it can be fixed.

After that it runs by itself.

---

### Good to know
- **Dec 16, 2026:** UO retires this class search page. You'll get a "Class monitor is
  failing" push when that happens. At that point the URL in `config.yaml` needs
  to be pointed at the new search.
- **Run frequency:** GitHub's free tier often runs scheduled jobs less often than
  every 10 minutes, just like with the lookouts.
- **To watch another course:** add it under `courses:` in `config.yaml`, for example
  `- "MATH 251"`. The `schedule_url` has to include that subject (right now it's
  STAT only).
- **To stop it:** Actions tab → Check class seats → **⋯ → Disable workflow**.
