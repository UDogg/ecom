import { rememberCategories } from "../components/Art";
import { createDemoApi } from "./demo";
import { httpApi } from "./http";
import type { Api } from "./types";

const backend: Api = __DEMO__ ? createDemoApi() : httpApi;

/** The backend: the BFF, or the in-browser demo in the GitHub Pages build (tree-shaken out of the other). */
export const api: Api = {
  ...backend,
  async products(q) {
    const page = await backend.products(q);
    rememberCategories(page.items);
    return page;
  },
  async product(id) {
    const p = await backend.product(id);
    rememberCategories([p]);
    return p;
  },
};
export * from "./types";
