/* Azure service icons used by the architecture diagram in the use-case modal.
   Each entry is an inline SVG string (48x48 viewBox) drawn in the Azure Fluent
   visual style with the service's brand colour family. Injected into the DOM as
   markup, so they are CSP-safe (no external requests, no inline style attrs).

   Exposed as window.AzureIcons:
     - icons[key]            -> svg string
     - iconFor(key)          -> svg string, falling back to a generic tile
     - iconForService(name)  -> svg string by Azure "serviceName" (cost meter)
*/
(function () {
  "use strict";

  // Shorthand to build an icon with a single vertical gradient + glyph.
  function svg(id, c0, c1, body) {
    return (
      '<svg viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">' +
      '<defs><linearGradient id="' + id + '" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="' + c0 + '"/>' +
      '<stop offset="1" stop-color="' + c1 + '"/></linearGradient></defs>' +
      body +
      "</svg>"
    );
  }

  var icons = {
    appservice: svg(
      "g_appservice", "#5EA0EF", "#0078D4",
      '<circle cx="24" cy="24" r="16" fill="url(#g_appservice)"/>' +
        '<path d="M24 8a16 16 0 0 0 0 32M8 24h32M24 8c-5 4-5 28 0 32M24 8c5 4 5 28 0 32" ' +
        'fill="none" stroke="#fff" stroke-width="1.6" opacity="0.9"/>'
    ),
    openai: svg(
      "g_openai", "#33B6A6", "#0E8C7E",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_openai)"/>' +
        '<path d="M24 14c5 0 8 4 6 8 2 4-1 8-6 8s-8-4-6-8c-2-4 1-8 6-8z" fill="none" ' +
        'stroke="#fff" stroke-width="1.8"/><circle cx="24" cy="24" r="2.4" fill="#fff"/>'
    ),
    cosmos: svg(
      "g_cosmos", "#5EA0EF", "#0F3D91",
      '<circle cx="24" cy="24" r="16" fill="url(#g_cosmos)"/>' +
        '<ellipse cx="24" cy="24" rx="16" ry="6.5" fill="none" stroke="#fff" stroke-width="1.6"/>' +
        '<ellipse cx="24" cy="24" rx="6.5" ry="16" fill="none" stroke="#fff" stroke-width="1.6"/>'
    ),
    monitor: svg(
      "g_monitor", "#5EA0EF", "#0078D4",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_monitor)"/>' +
        '<path d="M12 30l6-8 5 6 4-12 5 14h4" fill="none" stroke="#fff" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    loganalytics: svg(
      "g_loganalytics", "#5EA0EF", "#0F62C4",
      '<rect x="10" y="8" width="24" height="32" rx="4" fill="url(#g_loganalytics)"/>' +
        '<path d="M15 16h12M15 21h12M15 26h7" stroke="#fff" stroke-width="1.6" stroke-linecap="round"/>' +
        '<circle cx="31" cy="31" r="6" fill="none" stroke="#fff" stroke-width="2"/>' +
        '<path d="M35 35l4 4" stroke="#fff" stroke-width="2" stroke-linecap="round"/>'
    ),
    vm: svg(
      "g_vm", "#5EA0EF", "#0078D4",
      '<rect x="7" y="10" width="34" height="22" rx="3" fill="url(#g_vm)"/>' +
        '<rect x="11" y="14" width="26" height="14" rx="1.5" fill="#fff" opacity="0.9"/>' +
        '<path d="M18 38h12M24 32v6" stroke="#0F3D91" stroke-width="2" stroke-linecap="round"/>'
    ),
    avd: svg(
      "g_avd", "#5EA0EF", "#0F62C4",
      '<rect x="6" y="11" width="24" height="16" rx="2.5" fill="url(#g_avd)"/>' +
        '<rect x="9" y="14" width="18" height="10" rx="1" fill="#fff" opacity="0.9"/>' +
        '<rect x="26" y="20" width="16" height="17" rx="2.5" fill="#0F62C4"/>' +
        '<rect x="29" y="23" width="10" height="8" rx="1" fill="#fff" opacity="0.9"/>'
    ),
    avs: svg(
      "g_avs", "#7FB2F2", "#0F4FA8",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_avs)"/>' +
        '<rect x="14" y="14" width="8" height="8" rx="1.5" fill="#fff"/>' +
        '<rect x="26" y="14" width="8" height="8" rx="1.5" fill="#fff" opacity="0.85"/>' +
        '<rect x="14" y="26" width="8" height="8" rx="1.5" fill="#fff" opacity="0.85"/>' +
        '<rect x="26" y="26" width="8" height="8" rx="1.5" fill="#fff" opacity="0.7"/>'
    ),
    storage: svg(
      "g_storage", "#5EA0EF", "#0F62C4",
      '<rect x="8" y="12" width="32" height="24" rx="3" fill="url(#g_storage)"/>' +
        '<path d="M8 20h32M8 28h32" stroke="#fff" stroke-width="1.4" opacity="0.85"/>' +
        '<circle cx="13" cy="16" r="1.4" fill="#fff"/><circle cx="13" cy="24" r="1.4" fill="#fff"/>' +
        '<circle cx="13" cy="32" r="1.4" fill="#fff"/>'
    ),
    sql: svg(
      "g_sql", "#5EA0EF", "#0F62C4",
      '<ellipse cx="24" cy="13" rx="13" ry="5" fill="url(#g_sql)"/>' +
        '<path d="M11 13v22c0 2.8 5.8 5 13 5s13-2.2 13-5V13" fill="url(#g_sql)"/>' +
        '<path d="M11 24c0 2.8 5.8 5 13 5s13-2.2 13-5" fill="none" stroke="#fff" stroke-width="1.4" opacity="0.8"/>'
    ),
    search: svg(
      "g_search", "#5EA0EF", "#0F62C4",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_search)"/>' +
        '<circle cx="22" cy="22" r="7" fill="none" stroke="#fff" stroke-width="2.4"/>' +
        '<path d="M28 28l6 6" stroke="#fff" stroke-width="2.6" stroke-linecap="round"/>'
    ),
    apim: svg(
      "g_apim", "#5EA0EF", "#0F62C4",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_apim)"/>' +
        '<circle cx="16" cy="24" r="3.5" fill="#fff"/><circle cx="32" cy="16" r="3.5" fill="#fff"/>' +
        '<circle cx="32" cy="32" r="3.5" fill="#fff"/>' +
        '<path d="M19 22.5l10-5M19 25.5l10 5" stroke="#fff" stroke-width="1.8"/>'
    ),
    appgateway: svg(
      "g_appgateway", "#5EA0EF", "#0F62C4",
      '<path d="M24 6l16 8v10c0 10-7 15-16 18-9-3-16-8-16-18V14z" fill="url(#g_appgateway)"/>' +
        '<path d="M16 24h16M24 16v16" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>'
    ),
    firewall: svg(
      "g_firewall", "#F2856B", "#C8443B",
      '<rect x="8" y="10" width="32" height="28" rx="3" fill="url(#g_firewall)"/>' +
        '<path d="M8 19h32M8 28h32M18 10v9M30 10v9M14 19v9M24 19v9M34 19v9M22 28v10M14 28v10M34 28v10" ' +
        'stroke="#fff" stroke-width="1.3" opacity="0.85"/>'
    ),
    bastion: svg(
      "g_bastion", "#5EA0EF", "#0F4FA8",
      '<path d="M24 6l15 6v9c0 9-6 14-15 17-9-3-15-8-15-17v-9z" fill="url(#g_bastion)"/>' +
        '<path d="M24 16v10M19 21h10" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/>'
    ),
    vnet: svg(
      "g_vnet", "#5EA0EF", "#0F62C4",
      '<circle cx="24" cy="11" r="4" fill="url(#g_vnet)"/>' +
        '<circle cx="12" cy="34" r="4" fill="url(#g_vnet)"/><circle cx="36" cy="34" r="4" fill="url(#g_vnet)"/>' +
        '<circle cx="24" cy="24" r="4" fill="url(#g_vnet)"/>' +
        '<path d="M24 15v5M22 27l-8 4M26 27l8 4" stroke="#0F62C4" stroke-width="2"/>'
    ),
    containerapps: svg(
      "g_containerapps", "#8A7BEF", "#5B3FC4",
      '<path d="M24 6l16 9v18l-16 9-16-9V15z" fill="url(#g_containerapps)"/>' +
        '<path d="M24 24l16-9M24 24v18M24 24L8 15" stroke="#fff" stroke-width="1.6" opacity="0.85"/>'
    ),
    aks: svg(
      "g_aks", "#5EA0EF", "#0F62C4",
      '<path d="M24 6l16 9v18l-16 9-16-9V15z" fill="url(#g_aks)"/>' +
        '<circle cx="24" cy="24" r="3" fill="#fff"/>' +
        '<path d="M24 24l0-9M24 24l8 5M24 24l-8 5" stroke="#fff" stroke-width="1.8"/>' +
        '<circle cx="24" cy="15" r="2.2" fill="#fff"/><circle cx="32" cy="29" r="2.2" fill="#fff"/>' +
        '<circle cx="16" cy="29" r="2.2" fill="#fff"/>'
    ),
    acr: svg(
      "g_acr", "#5EA0EF", "#0F62C4",
      '<rect x="8" y="14" width="32" height="20" rx="3" fill="url(#g_acr)"/>' +
        '<rect x="13" y="19" width="6" height="6" rx="1" fill="#fff"/>' +
        '<rect x="21" y="19" width="6" height="6" rx="1" fill="#fff" opacity="0.85"/>' +
        '<rect x="29" y="19" width="6" height="6" rx="1" fill="#fff" opacity="0.7"/>'
    ),
    functions: svg(
      "g_functions", "#FFD15C", "#F0A30A",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_functions)"/>' +
        '<path d="M26 12l-9 13h6l-3 11 11-15h-6z" fill="#fff"/>'
    ),
    logicapps: svg(
      "g_logicapps", "#6FB1F0", "#0F62C4",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_logicapps)"/>' +
        '<circle cx="16" cy="16" r="3" fill="#fff"/><circle cx="32" cy="24" r="3" fill="#fff"/>' +
        '<circle cx="16" cy="32" r="3" fill="#fff"/>' +
        '<path d="M19 17l10 6M19 31l10-6" stroke="#fff" stroke-width="1.8"/>'
    ),
    servicebus: svg(
      "g_servicebus", "#5EA0EF", "#0F62C4",
      '<rect x="7" y="14" width="34" height="14" rx="3" fill="url(#g_servicebus)"/>' +
        '<path d="M11 28v5M37 28v5" stroke="#0F62C4" stroke-width="2"/>' +
        '<circle cx="14" cy="21" r="2" fill="#fff"/><circle cx="24" cy="21" r="2" fill="#fff"/>' +
        '<circle cx="34" cy="21" r="2" fill="#fff"/>'
    ),
    eventhubs: svg(
      "g_eventhubs", "#5EA0EF", "#0F62C4",
      '<circle cx="24" cy="24" r="7" fill="url(#g_eventhubs)"/>' +
        '<circle cx="24" cy="24" r="15" fill="none" stroke="#0F62C4" stroke-width="2" opacity="0.6"/>' +
        '<path d="M24 9v6M24 33v6M9 24h6M33 24h6" stroke="#0F62C4" stroke-width="2" stroke-linecap="round"/>'
    ),
    streamanalytics: svg(
      "g_streamanalytics", "#5EA0EF", "#0F62C4",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_streamanalytics)"/>' +
        '<path d="M12 18c6 0 6 4 12 4s6-4 12-4M12 28c6 0 6 4 12 4s6-4 12-4" ' +
        'fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round"/>'
    ),
    datafactory: svg(
      "g_datafactory", "#5EA0EF", "#0F62C4",
      '<circle cx="18" cy="20" r="8" fill="url(#g_datafactory)"/>' +
        '<circle cx="18" cy="20" r="3" fill="#fff"/>' +
        '<circle cx="32" cy="30" r="6" fill="url(#g_datafactory)"/>' +
        '<circle cx="32" cy="30" r="2.2" fill="#fff"/>'
    ),
    databricks: svg(
      "g_databricks", "#FF6F47", "#D6431B",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_databricks)"/>' +
        '<path d="M14 20l10 5 10-5M14 27l10 5 10-5" fill="none" stroke="#fff" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    frontdoor: svg(
      "g_frontdoor", "#5EA0EF", "#0F62C4",
      '<circle cx="24" cy="24" r="16" fill="url(#g_frontdoor)"/>' +
        '<path d="M8 24h32M24 8a22 22 0 0 1 0 32M24 8a22 22 0 0 0 0 32" ' +
        'fill="none" stroke="#fff" stroke-width="1.4" opacity="0.85"/>' +
        '<rect x="19" y="18" width="10" height="14" rx="1.5" fill="#fff"/>'
    ),
    expressroute: svg(
      "g_expressroute", "#5EA0EF", "#0F62C4",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_expressroute)"/>' +
        '<circle cx="15" cy="24" r="3.5" fill="#fff"/><circle cx="33" cy="24" r="3.5" fill="#fff"/>' +
        '<path d="M18.5 24h11M26 20l4 4-4 4" fill="none" stroke="#fff" stroke-width="2" ' +
        'stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    keyvault: svg(
      "g_keyvault", "#5EA0EF", "#0F62C4",
      '<path d="M24 6l15 6v9c0 9-6 14-15 17-9-3-15-8-15-17v-9z" fill="url(#g_keyvault)"/>' +
        '<circle cx="24" cy="21" r="4" fill="none" stroke="#fff" stroke-width="2.2"/>' +
        '<path d="M24 25v7M24 32h4" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/>'
    ),
    client: svg(
      "g_client", "#A6B0C0", "#6B7587",
      '<circle cx="24" cy="18" r="7" fill="url(#g_client)"/>' +
        '<path d="M11 39c0-8 6-12 13-12s13 4 13 12z" fill="url(#g_client)"/>'
    ),
    "default": svg(
      "g_default", "#5EA0EF", "#0F62C4",
      '<rect x="8" y="8" width="32" height="32" rx="8" fill="url(#g_default)"/>' +
        '<path d="M24 6l16 9v18l-16 9-16-9V15z" fill="none" stroke="#fff" stroke-width="1.6" opacity="0.8"/>'
    ),
  };

  // Azure cost-meter serviceName -> icon key.
  var byService = {
    "azure app service": "appservice",
    "cognitive services": "openai",
    "azure openai": "openai",
    "azure cognitive search": "search",
    "azure cosmos db": "cosmos",
    "azure monitor": "monitor",
    "application insights": "monitor",
    "log analytics": "loganalytics",
    "virtual machines": "vm",
    "storage": "storage",
    "sql database": "sql",
    "api management": "apim",
    "application gateway": "appgateway",
    "azure firewall": "firewall",
    "azure bastion": "bastion",
    "virtual network": "vnet",
    "azure container apps": "containerapps",
    "azure kubernetes service": "aks",
    "container registry": "acr",
    "functions": "functions",
    "logic apps": "logicapps",
    "service bus": "servicebus",
    "event hubs": "eventhubs",
    "stream analytics": "streamanalytics",
    "azure data factory v2": "datafactory",
    "azure databricks": "databricks",
    "azure front door service": "frontdoor",
    "specialized compute": "avs",
    "expressroute": "expressroute",
    "key vault": "keyvault",
  };

  function iconFor(key) {
    return icons[key] || icons["default"];
  }
  function iconForService(name) {
    if (!name) return icons["default"];
    return iconFor(byService[String(name).toLowerCase().trim()]);
  }
  // Human-friendly product names for icon keys (shared vocabulary with
  // updates.json's services map). Falls back to the key itself, title-cased.
  var keyLabels = {
    acr: "Container Registry",
    aks: "Azure Kubernetes Service",
    apim: "API Management",
    appgateway: "Application Gateway",
    appservice: "App Service",
    avs: "Azure VMware Solution",
    bastion: "Azure Bastion",
    containerapps: "Container Apps",
    cosmos: "Azure Cosmos DB",
    databricks: "Azure Databricks",
    datafactory: "Data Factory",
    eventhubs: "Event Hubs",
    expressroute: "ExpressRoute",
    firewall: "Azure Firewall",
    frontdoor: "Azure Front Door",
    functions: "Azure Functions",
    keyvault: "Key Vault",
    loganalytics: "Log Analytics",
    logicapps: "Logic Apps",
    monitor: "Azure Monitor",
    openai: "Azure OpenAI",
    search: "Azure AI Search",
    servicebus: "Service Bus",
    sql: "Azure SQL Database",
    storage: "Azure Storage",
    streamanalytics: "Stream Analytics",
    vm: "Virtual Machines",
    vnet: "Virtual Network",
  };
  function labelForKey(key) {
    if (!key) return "";
    if (keyLabels[key]) return keyLabels[key];
    return String(key).replace(/(^|\s)\S/g, function (c) { return c.toUpperCase(); });
  }
  // Icon key for an Azure cost-meter serviceName (e.g. "appservice").
  function keyForService(name) {
    if (!name) return "default";
    return byService[String(name).toLowerCase().trim()] || "default";
  }

  // Architecture tier per icon key, used to lay services out as a flow:
  //   edge (ingress/network) -> compute -> data (backing stores) -> obs.
  var tierByKey = {
    frontdoor: "edge", appgateway: "edge", apim: "edge", firewall: "edge",
    bastion: "edge", vnet: "edge", expressroute: "edge",
    appservice: "compute", containerapps: "compute", aks: "compute",
    functions: "compute", logicapps: "compute", vm: "compute",
    avd: "compute", avs: "compute", streamanalytics: "compute", datafactory: "compute",
    databricks: "compute",
    openai: "data", search: "data", cosmos: "data", sql: "data", storage: "data",
    servicebus: "data", eventhubs: "data", keyvault: "data", acr: "data",
    monitor: "obs", loganalytics: "obs",
    client: "client", "default": "data",
  };
  function tierForService(name) {
    return tierByKey[keyForService(name)] || "data";
  }

  window.AzureIcons = {
    icons: icons,
    iconFor: iconFor,
    iconForService: iconForService,
    keyForService: keyForService,
    labelForKey: labelForKey,
    tierForService: tierForService,
  };
})();
