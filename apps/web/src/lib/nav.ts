// Path-based (History API) navigation so every page has a real, crawlable URL for SEO.

export function navigate(path: string): void {
  if (typeof window === "undefined") return;
  window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

export function currentPath(): string {
  if (typeof window === "undefined") return "/";
  return window.location.pathname || "/";
}
