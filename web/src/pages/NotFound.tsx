import { Icon } from "../components/Icon";
import { useTitle } from "../lib/hooks";
import { href } from "../lib/router";

export function NotFound({ what = "page" }: { what?: string }) {
  useTitle("Not found");
  return (
    <div class="wrap empty">
      <Icon name="search" size={48} />
      <h1>We could not find that {what}</h1>
      <p class="muted">It may have moved, or it never existed.</p>
      <a class="btn btn-dark" href={href("/")}>Back to the shop</a>
    </div>
  );
}
