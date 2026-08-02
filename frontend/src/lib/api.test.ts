import { describe, it, expect, vi, beforeEach } from "vitest";
import { api, setToken } from "./api";

function mockFetchOnce(body: unknown, ok = true) {
  const fetchMock = vi.fn().mockResolvedValue({
    ok,
    statusText: ok ? "OK" : "Error",
    json: () => Promise.resolve(body),
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("api — session-source and tab endpoints", () => {
  beforeEach(() => { setToken(""); });

  it("attachSessionSource posts to /api/files/{sid}/sources/session with the source sid in the body", async () => {
    const fetchMock = mockFetchOnce({ name: "ref", columns: ["A"], row_count: 2 });
    const result = await api.attachSessionSource("target-sid", "ref", "source-sid");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/files/target-sid/sources/session");
    expect(opts.method).toBe("POST");
    expect(JSON.parse(opts.body)).toEqual({ name: "ref", source_sid: "source-sid" });
    expect(result).toEqual({ name: "ref", columns: ["A"], row_count: 2 });
  });

  it("dropSession issues a DELETE to /api/files/{sid}", async () => {
    const fetchMock = mockFetchOnce({ dropped: "s1" });
    await api.dropSession("s1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, opts] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/files/s1");
    expect(opts.method).toBe("DELETE");
  });

  it("sends the bearer token once set, on a subsequent call", async () => {
    setToken("tok-123");
    const fetchMock = mockFetchOnce({ name: "ref", columns: [], row_count: 0 });
    await api.attachSessionSource("t", "ref", "s");

    const [, opts] = fetchMock.mock.calls[0];
    expect(opts.headers.Authorization).toBe("Bearer tok-123");
  });
});
