import { render } from "preact";
import { App } from "./app";
import { refreshMe } from "./state/session";
import { applyTheme } from "./state/theme";
import "./styles/global.css";

applyTheme();
refreshMe();
render(<App />, document.getElementById("app")!);
