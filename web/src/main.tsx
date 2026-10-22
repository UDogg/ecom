import { render } from "preact";
import { App } from "./app";
import { trackVisit } from "./lib/telemetry";
import { refreshMe } from "./state/session";
import { applyTheme } from "./state/theme";
import "./styles/global.css";

applyTheme();
refreshMe();
trackVisit();
render(<App />, document.getElementById("app")!);
