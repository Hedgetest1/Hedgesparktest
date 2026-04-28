// HedgeSpark — Post-Purchase Survey (Order-Status target binding)
//
// Extension target: customer-account.order-status.block.render
// Component logic lives in SurveyCard.jsx; this file only binds.
// SurveyCard reads merchant config and silently hides itself when
// the Pro toggle `disabled_on_order_status` is true.

import {reactExtension} from "@shopify/ui-extensions-react/checkout";
import SurveyCard from "./SurveyCard.jsx";

export default reactExtension("customer-account.order-status.block.render", (api) => (
  <SurveyCard api={api} surface="order-status" />
));
