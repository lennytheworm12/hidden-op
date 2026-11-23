# Run the collector 24/7 on a free server

This moves the collector (na1, euw1, kr) off your PC onto a free cloud VM. When
it's done:

- Three collectors run around the clock and restart themselves after crashes
  and reboots.
- Every 3 hours, raw match JSON is uploaded to Google Drive in chunks
  (`My Drive/hidden-op/raw/`), checked against its MD5, and only then deleted
  from the server. Match IDs stay on the server, so no match is fetched twice.
- Once a day, a slim copy of the database (everything the model needs) is
  uploaded to `My Drive/hidden-op/hiddenop-slim.db`. With Google Drive for
  Desktop it shows up on your PC by itself.

**Main option: Oracle Cloud Always Free** (ARM VM, up to 200 GB disk, $0).
**Fallback: Google Cloud e2-micro** (30 GB disk, $0), at the end of this page.

Time needed: about 30–45 minutes, most of it Oracle's sign-up.

Every command below is meant to be copied and pasted as is. Lines in
`PowerShell` blocks go on your Windows PC; lines in `bash` blocks go on the
server after you've connected to it with `ssh`.

---

## 1. Create the Oracle Cloud account

1. Go to <https://signup.cloud.oracle.com> and sign up. It needs a credit or
   debit card for identity checks. Oracle places a small temporary hold and
   does not charge for Always Free resources.
2. **Home region:** pick one close to you (any works for this). You can't change
   it later, and Always Free VMs can only be created in it. If you have a
   choice, a less popular region (not Ashburn or Phoenix) is less likely to
   say "out of capacity" in step 3.
3. Wait for the "your account is ready" email (minutes, sometimes an hour or
   two), then sign in at <https://cloud.oracle.com>.

**Recommended: upgrade to Pay As You Go** (Billing & Cost Management → Upgrade
and Manage Payment). Always Free resources stay free after upgrading. Two
reasons it matters here:
- Oracle can reclaim Always Free VMs on free-tier accounts that look idle
  (very low CPU, network and memory use over 7 days). A collector that mostly
  waits on Riot's rate limit can look idle. Pay As You Go accounts are exempt.
- "Out of capacity" errors for ARM VMs are much rarer after upgrading.

If you upgrade, also create a budget alert so you'd hear about any charge:
Billing & Cost Management → Budgets → Create Budget, amount `1` (USD), alert at
1% of actual spend, your email. Then keep to the shapes and sizes in step 3,
which stay inside Always Free.

## 2. Make an SSH key on your PC

The key lets you log in to the server. In PowerShell:

```powershell
ssh-keygen -t ed25519 -f "$HOME\.ssh\hiddenop"
```

Press Enter twice when it asks for a passphrase (empty is fine for this). Then
copy the public half to your clipboard:

```powershell
Get-Content "$HOME\.ssh\hiddenop.pub" | Set-Clipboard
```

## 3. Create the VM

In the Oracle console: ☰ menu → **Compute** → **Instances** → **Create instance**.

| Setting | Choose |
|---|---|
| Name | `hidden-op` |
| Image | **Change image** → **Canonical Ubuntu** → **24.04** (not "Minimal") |
| Shape | **Change shape** → **Ampere** → `VM.Standard.A1.Flex`, **1 OCPU, 6 GB** memory (look for the "Always Free-eligible" tag) |
| Networking | Leave the defaults (new virtual cloud network, **Assign a public IPv4 address**: yes) |
| Add SSH keys | **Paste public keys** → paste (Ctrl+V) what you copied in step 2 |
| Boot volume | Tick **Specify a custom boot volume size** → `200` GB |

Click **Create**. When the state turns green (**Running**), copy the **Public
IP address** from the instance page.

If you get **"Out of capacity for shape VM.Standard.A1.Flex"**: try a
different **Availability domain** in the Placement section, or try again in a
few hours. Upgrading to Pay As You Go (step 1) usually fixes it.

1 OCPU and 6 GB is plenty for the collector. Always Free allows up to 4 OCPU,
24 GB and 200 GB of disk in total, so you can grow it later.

## 4. Connect to the server

In PowerShell, replace `1.2.3.4` with your Public IP:

```powershell
ssh -i "$HOME\.ssh\hiddenop" ubuntu@1.2.3.4
```

Type `yes` the first time. The prompt changes to `ubuntu@hidden-op:~$`: you're
on the server. Type `exit` to leave. Everything keeps running after you leave.

## 5. Get the code onto the server

**If the GitHub repo is private** (the default), give the server a read-only
key for it first. On the server:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/github -N "" -q
printf 'Host github.com\n  IdentityFile ~/.ssh/github\n' >> ~/.ssh/config
cat ~/.ssh/github.pub
```

Copy the line it prints (starts with `ssh-ed25519`). Open
<https://github.com/lennytheworm12/hidden-op/settings/keys/new>, Title
`oracle server`, paste it into Key, leave "Allow write access" unticked, click
**Add key**. Then on the server:

```bash
git clone -b claude/project-thread-n2mpzu git@github.com:lennytheworm12/hidden-op.git
```

Type `yes` if asked about github.com's fingerprint.

**If the repo is public**, just run:

```bash
git clone -b claude/project-thread-n2mpzu https://github.com/lennytheworm12/hidden-op.git
```

(Once the server PR is merged, use `-b elo-brackets` instead, or switch later
with `cd ~/hidden-op && git checkout elo-brackets && git pull`.)

## 6. Run the setup script

On the server:

```bash
cd ~/hidden-op && bash deploy/setup.sh
```

It installs everything, then asks for two things:

1. **Your Riot API key.** Paste it and press Enter. It's checked against Riot
   and saved in `~/hidden-op/.env`, readable only by you. It never goes to git.
2. **A Google Drive token.** The server has no browser, so you sign in on your
   PC. The script prints these instructions too. In a **new PowerShell window
   on your PC**:

   ```powershell
   winget install Rclone.Rclone
   ```

   Close that window and open a new one (so `rclone` is found), then:

   ```powershell
   rclone authorize "drive" "eyJzY29wZSI6ImRyaXZlLmZpbGUifQ"
   ```

   A browser opens. Sign in with the Google account that has the 2 TB and click
   **Allow**. PowerShell then prints a long line starting with
   `{"access_token":` between `Paste the following into your remote machine --->`
   and `<---End paste`. Copy that one line, from `{` to `}`, go back to the
   server window and paste it at the `Token:` prompt.

   The odd string after `"drive"` limits rclone to files it creates itself
   (`drive.file` access), so the server can't see the rest of your Drive.

At the end you'll see `na1 active`, `euw1 active`, `kr active`. That's it. You
can close PowerShell; the collectors keep going.

## 7. Check on it (any time)

Connect (step 4), then:

```bash
cd ~/hidden-op && bash deploy/status.sh
```

It shows whether each collector is running, disk space, matches per region,
tier and patch, how much raw JSON has gone to Drive, and recent errors.

Other handy commands on the server:

```bash
journalctl -u hiddenop-collector@na1 -f         # watch one region live (Ctrl+C to stop watching)
sudo systemctl start hiddenop-archive            # upload raw JSON to Drive now
sudo systemctl start hiddenop-snapshot           # upload the slim DB to Drive now
sudo systemctl stop 'hiddenop-collector@*'       # stop collecting
sudo systemctl start 'hiddenop-collector@*'      # start again (they resume where they left off)
```

**Stop the collectors on your PC** once the server is running: they'd only
spend the same API budget on a separate database.

## 8. Update the code later

When new collector code is pushed to GitHub:

```bash
cd ~/hidden-op && git pull && bash deploy/setup.sh
```

It keeps your key and Drive sign-in, and restarts the collectors.

## 9. Use the data on your PC

With Google Drive for Desktop running, `My Drive\hidden-op\hiddenop-slim.db`
syncs to your PC daily. Copy it out of the Drive folder before working on it
(Drive may replace it mid-run), then point the model at it. In PowerShell, from
your `hidden-op` folder (Drive is usually `G:`; adjust if yours differs):

```powershell
Copy-Item "G:\My Drive\hidden-op\hiddenop-slim.db" data\server.db
python scripts/db_status.py --db data\server.db
python scripts/run_model.py --db data\server.db --tiers EMERALD
```

Raw match JSON lives in `My Drive\hidden-op\raw\` as SQLite files
(`raw-<date>-<id>.db`, ~25,000 matches each). Each row of their `raw` table is
`match_id, platform, patch, tier, game_start, raw`, where `raw` is the match
JSON compressed with zlib: `json.loads(zlib.decompress(raw))` in Python. Each
match in the slim DB records which chunk holds its JSON in `matches.archive`.

## How much disk and Drive it uses

Rough guide; `status.sh` shows the real numbers.

- Raw JSON: on the order of 1 GB a day per region, all of it shipped to Drive.
  2 TB holds well over a year.
- Slim tables on the server grow by a few hundred MB a day across the three
  regions. On Oracle's 200 GB disk that's many months. The daily snapshot needs
  free space about the size of the DB; it skips itself (and logs why) if
  there isn't room.

---

## Fallback: Google Cloud e2-micro

Use this if Oracle won't give you a VM. It's free within the
[free tier](https://cloud.google.com/free/docs/free-cloud-features#compute) but
has 1 GB of RAM and 30 GB of disk, so the server fills up after some weeks of
three-region collecting (the slim data stays; check `status.sh`).

1. Go to <https://console.cloud.google.com>, create a project, and enable
   billing (card required; the free tier still applies).
2. ☰ → **Compute Engine** → **VM instances** → **Create instance** (click
   Enable if it asks to enable the API).
3. Settings:

   | Setting | Choose |
   |---|---|
   | Region | `us-west1`, `us-central1` or `us-east1` (only these are free) |
   | Machine type | **E2** → `e2-micro` |
   | Boot disk | **Change** → Ubuntu → **Ubuntu 24.04 LTS** (x86/64, not Arm), disk type **Standard persistent disk**, size `30` GB |

   Leave the rest as is and click **Create**. (A "balanced" disk or a
   different region is not free.)
4. Click **SSH** next to the VM: a terminal opens in your browser, no keys
   needed. Then do steps 5–7 above in that window. Your username there is not
   `ubuntu`, which is fine: the setup script uses whoever runs it, and adds a
   swap file because of the small RAM.

Uploads from Google Cloud to Google Drive don't count as paid network egress
as far as we know, but check the first month's billing page to be sure.
