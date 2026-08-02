import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// No `globals: true` in vitest.config.ts, so React Testing Library's own
// auto-cleanup (which relies on detecting a global `afterEach`) never
// registers — do it explicitly instead, once, for every test file.
afterEach(cleanup);
