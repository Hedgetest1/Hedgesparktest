// HedgeSpark — Post-Purchase Survey (Thank-You target binding)
//
// Extension target: purchase.thank-you.block.render
// Component logic lives in SurveyCard.jsx; this file only binds.

import {reactExtension} from "@shopify/ui-extensions-react/checkout";
import SurveyCard from "./SurveyCard.jsx";

export default reactExtension("purchase.thank-you.block.render", (api) => (
  <SurveyCard api={api} surface="thank-you" />
));
