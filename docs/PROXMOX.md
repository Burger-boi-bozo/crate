# Move the Crate converter to Proxmox

Run the installer in **Proxmox → your node → Shell** as root. The management address for this installation is `https://10.0.0.50:8006/`; it stays on your LAN. A cloud assistant cannot directly reach that private address.

```bash
curl -fL https://raw.githubusercontent.com/Burger-boi-bozo/crate/main/scripts/proxmox-install.py -o /root/crate-proxmox.py
python3 /root/crate-proxmox.py
```

The installer creates a **new Debian 13 VM**, selects an unused VM ID and active storage with sufficient space, verifies the official cloud-image checksum, and installs the public converter. Defaults: **2 cores, 2 GB RAM, 24 GB disk, vmbr0 with DHCP**, and automatic startup with Proxmox. It does not install application packages on the hypervisor, replace existing guests, change bridge settings, or remove Render.

Use `python3 /root/crate-proxmox.py --plan` for a read-only preflight. Options include `--storage local-lvm`, `--bridge vmbr0`, `--memory 4096`, `--cores 4`, `--disk 32`, and `--vmid 120`. A selected VM ID must be unused. Keep this VM on its original node unless you also move its generated cloud-init snippet storage.

The installer checks the anonymous session and converts/downloads a small public CC0 test video inside the VM. It prints the VM's LAN URL only after that test succeeds. Installation usually takes 10–20 minutes, depending on downloads and your hardware. The installer has been checked with automated preflight tests; execution on your particular Proxmox host still needs this local step.

## Connect the public hostname

After setup, choose **Y** when asked to open Cloudflare setup:

1. Enter the exact subdomain you own. The existing domain discussed for this project is `dpifiles.org`; `dpiflies.org` is a different spelling, so check it carefully.
2. Open the Cloudflare authorization URL printed by the VM in your browser and select your domain. Credentials stay on your VM and are never pasted into chat.
3. The helper creates a named tunnel, routes that hostname to Crate, enables secure browser cookies, and starts the tunnel at boot. It exposes only the converter on port 8080 through the tunnel.

If a DNS record already exists, the helper stops instead of overwriting it. Review that exact hostname's record before cutover, then rerun the helper. Do not remove records for other services. The saved command is printed as `/root/crate-connect-VMID.sh` on the Proxmox host.

No router port forwarding or public Proxmox dashboard is needed. Cloudflare must manage DNS for the domain. No paid Cloudflare Workers, R2, database, or Render instance is added.

## Verify the move

Open the HTTPS hostname and test a short public clip, download the completed file, and check that it plays. Try YouTube separately: using your home connection changes the download origin, but does not guarantee that every site or restricted video will work. Spotify and Apple Music subscription tracks remain unsupported as full portable music files.

The app has no access code and supports MP4 up to 1080p, MP3, private browser queues, and browser history. Existing bounds remain: 10-minute clips, 100 MB output files, one conversion at a time, and 10 submissions per rolling day. Files expire after an hour or on app restart; this move does not convert the in-memory queue into a persistent database.

Render's temporary jobs/files are not transferred automatically. History is saved per browser and hostname. Verify the new site and save any needed old files before suspending or deleting the old Render service. DNS cutover and Render retirement are not performed by the VM creation step.

## Maintenance

The host saves `/root/crate-vm-VMID.json` with the VM ID and installation commit. The dedicated SSH key and verified guest host key are under `/root/.ssh/crate-vm-VMID*`. Keep them private. Inside the VM:

```bash
sudo systemctl status crate crate-tunnel
sudo journalctl -u crate -n 100 --no-pager
sudo journalctl -u crate-tunnel -n 100 --no-pager
```

If bootstrap fails, the VM is left in place for inspection. Use the exact diagnostic command printed by the installer; do not repeatedly create more VMs. Cloud-init output is in `/var/log/cloud-init-output.log` inside that VM. A new VM's console login is locked by design; use the dedicated SSH key or Proxmox guest-agent commands.

References: [Proxmox cloud-init](https://pve.proxmox.com/wiki/Cloud-Init_Support), [Debian cloud images](https://cloud.debian.org/images/cloud/trixie/latest/), [Cloudflare local tunnel setup](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/create-local-tunnel/).
