# Setting up the off-host backup mirror (Backblaze B2)

**Status as of 2026-08-23: the code is in place; the bucket and the credentials
are not.** Until you complete the steps below, `python src/health.py` on the
Bizon reports `mirror=never OVERDUE` and exits non-zero. That is correct — it
is telling you the durable copy does not exist yet, which has been true since
the 2026-08-20 cutover and was silent until now.

## Why this exists

The Bizon has **one** 6.9T NVMe. `~/bots/repete/backups/` sits on the same disk
as `memory/`, so it protects against `rm -rf memory` and against nothing else —
not the disk, not the machine, not the site. Before this, `scripts/backup.sh`
only knew how to mirror to an iCloud path, which exists on a Mac and not on
Linux, so every weekday run skipped it with a warning to stderr that nothing
read.

The laptop cannot be the answer: it sleeps, and a mirror that only runs when a
lid is open is not a 24/7 mirror.

## What only you can do

Three things, in this order. **None of them should ever be pasted into a chat
transcript.**

### 1. Create the bucket and an application key

At <https://www.backblaze.com> → B2 Cloud Storage. The free tier is 10 GB; this
uses roughly **45 MB** (about 1.5 MB per weekday, 30 retained), so it stays free
indefinitely at this volume.

- Create a **private** bucket, e.g. `repete-backups`.
- Create an **application key scoped to that bucket only** — not a master key.
  A key that can only write one bucket is the whole point; a master key on a
  trading host is a much larger blast radius than the thing it protects.

### 2. Configure rclone on the Bizon

```bash
ssh bizon
cd ~/bots/repete
docker compose exec agent rclone config
```

Choose `n` (new remote), name it **`b2`**, storage type **`b2`**, and paste the
key id and application key at the prompts. The config lands inside the
container, so make it survive rebuilds by keeping it on the host instead:

```bash
mkdir -p ~/bots/repete/rclone
docker compose exec agent cat /root/.config/rclone/rclone.conf \
  > ~/bots/repete/rclone/rclone.conf
chmod 600 ~/bots/repete/rclone/rclone.conf
```

then add the mount to `docker-compose.yml` under the agent's `volumes:`

```yaml
      - ./rclone:/root/.config/rclone
```

### 3. Generate the passphrase — and put it somewhere you will still have it

The archive is encrypted with `gpg --symmetric` before it leaves the box.
**Nobody but you will ever have this passphrase, including me.** Lose it and
every mirrored archive is unrecoverable ciphertext.

```bash
ssh bizon
cd ~/bots/repete
umask 077
printf 'REPETE_BACKUP_PASSPHRASE=%s\n' "$(openssl rand -base64 48)" >> .env
printf 'REPETE_MIRROR_REMOTE=b2:repete-backups\n' >> .env
grep REPETE_BACKUP_PASSPHRASE .env      # copy this into your password manager NOW
```

**Put it in your password manager before you close that terminal.** The
passphrase exists in exactly two places: the Bizon's `.env`, and wherever you
put it. If the Bizon is the thing that died, `.env` died with it — which is the
entire scenario this mirror is for.

## Verify it, rather than assuming

```bash
ssh bizon
cd ~/bots/repete
docker compose up -d --build          # rclone + gnupg enter the image here
docker compose exec agent sh scripts/backup.sh
```

You should see, in order:

```
wrote backups/agent-backup-<stamp>.tar.gz (1.5M)
mirrored b2:repete-backups/agent-backup-<stamp>.tar.gz.gpg (sha1 verified)
receipt memory/offhost_mirror.json
```

`sha1 verified` means the hash **B2 itself reports** for the stored object
matches the bytes that were uploaded — not that `rclone` exited zero. An empty
hash is treated as a failure, not a pass.

Then confirm health agrees:

```bash
docker compose exec agent python src/health.py
```

`mirror=0.0h` and no `OVERDUE`. If it still says `never`, the receipt was not
written, which means a step above failed — read the backup output rather than
re-running blindly.

## Prove you can actually restore

A backup that has never been restored is a hope. Decrypt one end to end:

```bash
cd /tmp
rclone copy b2:repete-backups/agent-backup-<stamp>.tar.gz.gpg .
gpg --batch --pinentry-mode loopback --passphrase-file <(grep -h '^REPETE_BACKUP_PASSPHRASE=' ~/bots/repete/.env | cut -d= -f2-) \
    --decrypt agent-backup-<stamp>.tar.gz.gpg > restored.tar.gz
tar -tzf restored.tar.gz | head
```

Do this **once now** and once whenever the passphrase changes. `scripts/restore_drill.py`
covers the local archive; it does not know about the remote.

## What breaks loudly, and what does not

- **A failed upload fails the backup job.** `backup.sh` exits non-zero rather
  than writing a receipt, so the mirror can never report a success it did not
  have.
- **A missed weekday mirror shows up in `health.py`** as `mirror=<n>h OVERDUE`,
  judged against whether a weekday 17:00 ET run was actually due — so a quiet
  weekend is not an alarm. See `tests/test_offhost_mirror_staleness.py`.
- **Setting `REPETE_MIRROR_REMOTE` without the passphrase refuses to run.** It
  will not upload the ledger in the clear because one variable was forgotten.
- **What does NOT break loudly:** the passphrase being wrong. gpg will happily
  encrypt to a passphrase you have mistyped, and you will only find out at
  restore time. That is what the restore drill above is for.
