# Ten hosting alternatives reviewed

Decision date: 12 September 2026. Goal: paste a public media link and get an MP4 or MP3 at `down.dpifiles.org`, with no active home server and no ongoing payment beyond the existing domain.

These are alternatives to the paid Cloudflare Containers/R2 design. They were compared; ten services were not provisioned. **Render Free is the prepared first deployment**, because the connected account can run Python plus FFmpeg and supports a custom domain. Hosted access to YouTube still needs a real test and can be blocked independently of hosting price.

| # | Alternative | Fit and decisive limitation | Official source |
| --- | --- | --- | --- |
| 1 | Render Free | Selected for a small shared converter. Custom domains supported; sleeps after 15 minutes, storage is temporary, shared quotas apply. No payment method is needed for suspension instead of bandwidth overage billing. | [Free service limits](https://render.com/docs/free) |
| 2 | Koyeb Free | Possible fallback: one small instance, 512 MB memory and 0.1 vCPU; sleeps when idle. Tight resources and account eligibility need checking. | [Instances](https://www.koyeb.com/docs/reference/instances) |
| 3 | Northflank Sandbox | Possible fallback: Sandbox offers two free services. Needs an account and its current sandbox resource/usage conditions verified before deployment. | [Pricing](https://northflank.com/pricing) |
| 4 | Railway Free | Free plan exists, but usage-based limits and a small resource budget need checking against conversion and download traffic. Do not assume free compute means unlimited media bandwidth. | [Plans](https://docs.railway.com/pricing/plans) |
| 5 | Oracle Always Free VM | Can run the full Docker stack. Capacity/eligibility and account setup are hurdles; idle instances can be reclaimed. More server maintenance than a small hosted service. | [Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm) |
| 6 | Google Cloud Run | Technically suitable for a containerized converter, with free monthly usage allowances. Excess usage is billed; build/registry costs also matter. Does not meet a strict no-billing requirement without further controls. | [Pricing](https://cloud.google.com/run/pricing) |
| 7 | AWS Lambda | Could run bounded conversion jobs, but file delivery/storage requires extra design and excess usage is billable. Poor fit for simple, guaranteed-zero setup. | [Pricing](https://aws.amazon.com/lambda/pricing/) |
| 8 | Fly.io | Can run Docker/FFmpeg, but its free trial is temporary rather than an ongoing free hosting plan. | [Free trial](https://fly.io/docs/about/free-trial/) |
| 9 | Hugging Face Spaces | Current creation rules require a paid subscription for ordinary Gradio/Docker compute Spaces. Free static hosting cannot run this Python/FFmpeg backend. | [Spaces overview](https://huggingface.co/docs/hub/spaces-overview) |
| 10 | Vercel Hobby | Good for a static frontend, but function duration and payload limits make direct media conversion/delivery a poor fit. Would still need a separate backend. | [Function limits](https://vercel.com/docs/functions/limitations) |

No option makes every media site reliably downloadable. Source sites can reject datacenter traffic, require authentication, or change extraction mechanisms. The app reports those failures, limits resource use, and supports a direct download provided by a creator through supported media hosts where available. It does not use random public conversion APIs or other people's compute.
