# **ZTB Zoom Endpoint Group & Network Object Sync**

Automated endpoint synchronization tool for **Zscaler Zero Trust Branch (ZTB)**. This utility extracts official Zoom IPv4 subnets, fully qualified domain names (FQDNs), and transport layer port ranges from Zoom's authoritative support knowledge base, exports a pipe-delimited CSV for bulk ingestion, and programmatically provisions or updates reusable Network and Port Objects in the ZTB Admin Console via REST API \[1\]\[2\].

## **Features**

* **Authoritative Zoom Extraction:** Automatically parses and normalizes Zoom meeting, phone, app, and CDN endpoints referencing Zoom Support KB0060548 \[1\].  
* **Strict Mask & FQDN Validation:**  
* Enforces valid CIDR format across all host IPs and networks (ensuring /32 host masks) to prevent silent iptables match drops in ZTB.  
* Normalizes malformed wildcard patterns like \*xxx.yyy.zzz into \*.yyy.zzz (wildcarding the entire first word) to comply with ZTB regex validation rules.  
* **Dual Ingestion Support:**  
* **Pipe-Delimited CSV:** Automatically generates ztb\_zoom\_network\_objects.csv using the pipe (\|) delimiter compatible with Mike Dechow's ztb-objects-bulk ingestion pattern and portal manual bulk upload \[3\].  
* **REST API Upsert:** Discovers existing objects by name to update them in place (PUT /api/v2/groups/{id}) or create them if missing (POST /api/v2/groups) \[4\].

## **Prerequisites & Dependencies**

### **Python Runtime**

* Python 3.10 or higher.

### **Required Python Libraries**

Install the required dependencies using pip:

pip install requests beautifulsoup4 python-dotenv  
Or define a requirements.txt file:

requests\>=2.31.0  
beautifulsoup4\>=4.12.0  
python-dotenv\>=1.0.0  
And install via:

pip install \-r requirements.txt

## **Environment Variables**

The script strictly requires your ZTB tenant URL and API key to be exported in your shell environment or defined in a local .env file located in the same directory as the script.

### **Configuration Reference**

| Variable | Required | Description | Example |
| :---- | :---- | :---- | :---- |
| ZTB\_BASE\_URL | **Yes** | Fully qualified REST API URL for your ZTB tenant (must include the \-api suffix). | https\://tenantname-api.goairgap.com |
| ZTB\_API\_KEY | **Yes** | Long-lived API key generated from the ZTB Admin Portal. | ztb\_live\_sec\_abc123... |

### **Setting Up .env**

Create a .env file alongside ztb\_zoom\_bulk\_sync.py:

\# ZTB Tenant REST API Base URL  
ZTB\_BASE\_URL="https\://\<your-tenant\>-api.goairgap.com"

\# ZTB API Key (Settings \> Global \> API Keys)  
ZTB\_API\_KEY="your\_actual\_api\_key\_here"  
Alternatively, export them directly in your shell session:

export ZTB\_BASE\_URL="https\://\<your-tenant\>-api.goairgap.com"  
export ZTB\_API\_KEY="your\_actual\_api\_key\_here"

***Note on UI Origin: The script automatically derives the Web UI origin (e.g. https\://tenantname.goairgap.com) by stripping \-api. from ZTB\_BASE\_URL to populate the Origin and Referer headers required by ZTB API gateways.***

## **REST API Endpoints Used**

The automation script interacts with the following REST API endpoints on your ZTB tenant:

### **1\. Authentication**

* **Endpoint:** POST /api/v3/api-key-auth/login  
* **Purpose:** Exchanges the tenant ZTB\_API\_KEY for a short-lived Bearer delegate\_token.  
* **Headers Required:** Origin, Referer, and X-Requested-With: XMLHttpRequest.

### **2\. Group Discovery (Existence Check)**

* **Endpoint:** GET /api/v2/groups?group\_type={type}\&group\_owner=user\&size=500  
* **Purpose:** Queries existing objects to detect whether Zoom-Required-IPs, Zoom-Required-FQDNs, or Zoom-Required-Ports already exist in the tenant.

### **3\. Object Creation**

* **Endpoint:** POST /api/v2/groups?refresh\_token=enabled  
* **Purpose:** Creates new user-defined Network or Port Object groups when not previously present in the tenant.

### **4\. Object In-Place Update**

* **Endpoint:** PUT /api/v2/groups/{group\_id}?refresh\_token=enabled  
* **Purpose:** Updates existing object definitions with refreshed IP prefixes, domains, and port lists without modifying the assigned group ID.

## **Created ZTB Objects**

The script maintains three core objects under **Resources \> Objects** in the ZTB Admin Console \[3\]\[4\]:

1. **Zoom-Required-IPs (type: "network"):**

* Contains Zoom's authoritative global meeting, phone, and media subnets (e.g. 3.7.35.0/25, 149.137.0.0/17, 170.114.0.0/16, etc.) \[1\].  
* Populates both member\_attributes.ip\_prefix and member\_attributes.ip\_prefix\_local.

2. **Zoom-Required-FQDNs (type: "network"):**

* Contains Zoom domain roots and wildcards (e.g. \*.zoom.us, \*.zoom.com, \*.zoomstatus.com, \*.zoomapp.cloud, zdmapi.zoom.us) \[1\]\[2\].  
* Populates both member\_attributes.fqdn and member\_attributes.fqdn\_local.

3. **Zoom-Required-Ports (type: "l4port"):**

* Contains Zoom signaling and real-time media port definitions \[1\].  
* TCP signaling: tcp:80,443,5091,8801,8802.  
* UDP real-time media/signaling: udp:3478,3479,8801-8810,20000-64000.

## **Usage Instructions**

### **1\. Run the Synchronization**

Execute the script from your terminal:

python3 ztb\_zoom\_bulk\_sync.py

### **2\. Expected Output**

\[\*\] Fetching Zoom Network Endpoints from: https\://support.zoom.com/hc/en/article?id=zm\_kb\&sysparm\_article=KB0060548  
\[+\] Successfully scraped Zoom Support Knowledge Base.  
\[+\] Extracted: 52 CIDRs, 6 FQDNs, 5 TCP port groups, 4 UDP port groups.  
\[\*\] Exporting bulk object file to ztb\_zoom\_network\_objects.csv (pipe-delimited)...  
\[+\] Successfully wrote 2 object rows to ztb\_zoom\_network\_objects.csv.  
\[+\] Authenticated to ZTB Management API.  
\[\*\] Updating existing network 'Zoom-Required-IPs' (ID: 142)...  
\[\*\] Updating existing network 'Zoom-Required-FQDNs' (ID: 143)...  
\[\*\] Updating existing l4port 'Zoom-Required-Ports' (ID: 144)...

\[+\] Object synchronization complete:  
    \- IP Object:   Zoom-Required-IPs   (ID: 142\)  
    \- FQDN Object: Zoom-Required-FQDNs (ID: 143\)  
    \- Port Object: Zoom-Required-Ports (ID: 144\)

### **3\. Verification**

4. Log into your Zscaler Admin Console.  
5. Navigate to **Zero Trust Branch \> Resources \> Objects**.  
6. Under the **User Defined Groups** tab, verify that Zoom-Required-IPs, Zoom-Required-FQDNs, and Zoom-Required-Ports reflect the populated counts.  
7. Reference these objects in your Firewall Policies or Forwarding Rules as needed.

## **Sources**

\[1\][Zoom network firewall or proxy server settings](https://support.zoom.com/hc/en/article?id=zm_kb&sysparm_article=KB0060548)

\[2\][Configuring Network Components for Zoom](https://library.zoom.com/admin-corner/network-management/quality-of-service-and-network-best-practices-explainer/configuring-network-components-for-zoom)

\[3\][Adding Objects \| Zscaler](https://help.zscaler.com/zero-trust-branch/adding-objects)

\[4\][About Objects \| Zscaler](https://help.zscaler.com/zero-trust-branch/about-objects)