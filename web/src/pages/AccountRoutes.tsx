import { Register, SignIn } from "./Account";

export function Account({ path }: { path: string }) {
  return path === "/register" ? <Register /> : <SignIn />;
}
