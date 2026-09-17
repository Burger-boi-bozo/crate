# RX 6700 XT GPU worker

Crate 6.1 can offload H.264 and HEVC transcodes to a dedicated internal worker while the public/controller VM remains isolated from the Proxmox host.

Production layout:

`Crate VM 101 (3 vCPU / 4 GB) -> 10.0.0.40:19081 -> LXC 105 crate-gpu -> /dev/dri/renderD128 -> RX 6700 XT VCN`

The worker accepts only the Crate controller IP and also requires a random bearer token. VM 101 reads that token from `/etc/crate/gpu-worker-token`; the token is not exposed through public APIs or the admin UI. Admin capabilities show only worker health, version, supported codecs, and encoder availability.

The host owns the Radeon with `amdgpu`. Do not simultaneously attach the whole PCI device to another VM. LXC 105 receives only the render node, not PCI or hypervisor access. If the worker is unavailable, busy, or a job is unsuitable for offload, Crate automatically uses its local software encoder.

Current RX 6700 XT policy: H.264 and HEVC are GPU-eligible. AV1/VP9 remain on the tested software path because `vainfo` advertises encode entrypoints for H.264/HEVC on this Navi 22 card.

The worker runs as the unprivileged `crategpu` account with membership in the container's `render` group. Proxmox device passthrough is configured as `dev0: path=/dev/dri/renderD128,gid=992,mode=0660` on this host.

Admin routing is controlled by **Processor priority** under `/admin` → Runtime settings. `GPU preferred` is the default and offloads eligible H.264/HEVC jobs; `CPU preferred` bypasses the worker without disabling or reconfiguring it. The setting is persisted in Crate SQLite and takes effect for newly processed jobs immediately.

On the controller, `/etc/crate` is `0710 root:crate`, `/etc/crate/environment` remains `0600 root:root`, and `/etc/crate/gpu-worker-token` is `0640 root:crate`. This grants the Crate service traverse/read access only to the dedicated GPU token without exposing the other root-owned secrets.
