import InternalApp from "./components/InternalApp";
import PublicInquiry from "./components/PublicInquiry";

export default function App() {
  return window.location.pathname.startsWith("/inquiry") ? <PublicInquiry /> : <InternalApp />;
}

