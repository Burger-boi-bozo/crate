import { Container, getContainer } from "@cloudflare/containers";

interface Env {
  CRATE_CONTAINER: DurableObjectNamespace<CrateContainer>;
  CRATE_STORAGE: R2Bucket;
  UDM_USERNAME: string;
  UDM_PASSWORD: string;
}

type Download = {
  id: string;
  status: string;
  relative_path?: string | null;
};

const INTERNAL_TOKEN = "crate-cloudflare-internal-v1";
const STATE_KEY = "state/downloads.db";

export class CrateContainer extends Container<Env> {
  defaultPort = 8080;
  requiredPorts = [8080];
  sleepAfter = "24h";
  enableInternet = true;
  envVars = {
    UDM_DATA_DIR: "/data",
    UDM_DOWNLOAD_DIR: "/downloads",
    UDM_MAX_CONCURRENT: "2",
    UDM_USERNAME: this.env.UDM_USERNAME,
    UDM_PASSWORD: this.env.UDM_PASSWORD,
    UDM_INTERNAL_TOKEN: INTERNAL_TOKEN,
  };

  override async onStart(): Promise<void> {
    const snapshot = await this.env.CRATE_STORAGE.get(STATE_KEY);
    if (snapshot?.body) {
      const response = await this.containerFetch("http://localhost/api/internal/state", {
        method: "PUT",
        headers: {
          "content-type": "application/vnd.sqlite3",
          "x-crate-internal": INTERNAL_TOKEN,
        },
        body: snapshot.body,
      });
      if (!response.ok) throw new Error(`Could not restore Crate state (${response.status})`);
    }
    await this.schedule(10, "syncLoop");
  }

  override async fetch(request: Request): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname.startsWith("/api/internal/")) {
      return new Response("Not found", { status: 404 });
    }

    const fileMatch = url.pathname.match(/^\/api\/downloads\/([^/]+)\/file$/);
    if (request.method === "GET" && fileMatch) {
      if (!this.isAuthorized(request)) return this.authRequired();
      const storedKey = await this.ctx.storage.get<string>(`file:${fileMatch[1]}`);
      if (storedKey) {
        const object = await this.env.CRATE_STORAGE.get(storedKey);
        if (object) {
          const headers = new Headers();
          object.writeHttpMetadata(headers);
          headers.set("etag", object.httpEtag);
          headers.set("content-length", String(object.size));
          return new Response(object.body, { headers });
        }
      }
    }

    const response = await this.containerFetch(request);
    if (response.ok && url.pathname.startsWith("/api/downloads") && request.method !== "GET") {
      try {
        await this.syncState();
      } catch (error) {
        console.error("Crate state sync failed", error);
      }
      this.renewActivityTimeout();
      await this.schedule(3, "syncLoop");
    }
    return response;
  }

  async syncLoop(): Promise<void> {
    try {
      const response = await this.containerFetch("http://localhost/api/downloads", {
        headers: this.appAuthHeaders(),
      });
      if (!response.ok) return;
      const jobs = (await response.json()) as Download[];
      let active = false;

      for (const job of jobs) {
        if (["queued", "downloading", "paused"].includes(job.status)) active = true;
        if (job.status !== "completed") continue;

        const mapKey = `file:${job.id}`;
        if (await this.ctx.storage.get(mapKey)) continue;
        const file = await this.containerFetch(`http://localhost/api/downloads/${job.id}/file`, {
          headers: this.appAuthHeaders(),
        });
        if (!file.ok || !file.body) continue;

        const name = this.safeFilename(
          this.responseFilename(file) || job.relative_path?.split("/").pop() || "download",
        );
        const objectKey = `downloads/${job.id}/${name}`;
        await this.env.CRATE_STORAGE.put(objectKey, file.body, {
          httpMetadata: {
            contentType: file.headers.get("content-type") || "application/octet-stream",
            contentDisposition: file.headers.get("content-disposition") || `attachment; filename="${name}"`,
          },
        });
        await this.ctx.storage.put(mapKey, objectKey);
      }

      await this.syncState();
      if (active) {
        this.renewActivityTimeout();
        await this.schedule(15, "syncLoop");
      }
    } catch (error) {
      console.error("Crate persistence sync failed", error);
      await this.schedule(30, "syncLoop");
    }
  }

  private async syncState(): Promise<void> {
    const response = await this.containerFetch("http://localhost/api/internal/state", {
      headers: { "x-crate-internal": INTERNAL_TOKEN },
    });
    if (response.ok && response.body) {
      await this.env.CRATE_STORAGE.put(STATE_KEY, response.body, {
        httpMetadata: { contentType: "application/vnd.sqlite3" },
      });
    }
  }

  private responseFilename(response: Response): string | null {
    const disposition = response.headers.get("content-disposition") || "";
    const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
    if (encoded) return decodeURIComponent(encoded[1]);
    const plain = disposition.match(/filename="?([^";]+)"?/i);
    return plain?.[1] || null;
  }

  private appAuthHeaders(): HeadersInit {
    return { authorization: `Basic ${this.basicToken()}` };
  }

  private isAuthorized(request: Request): boolean {
    return request.headers.get("authorization") === `Basic ${this.basicToken()}`;
  }

  private authRequired(): Response {
    return new Response("Authentication required", {
      status: 401,
      headers: { "www-authenticate": 'Basic realm="Crate"' },
    });
  }

  private basicToken(): string {
    const bytes = new TextEncoder().encode(`${this.env.UDM_USERNAME}:${this.env.UDM_PASSWORD}`);
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary);
  }

  private safeFilename(name: string): string {
    return name.replace(/[\\/\u0000-\u001f\u007f]/g, "_").slice(0, 240) || "download";
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    return getContainer(env.CRATE_CONTAINER, "primary").fetch(request);
  },
};
