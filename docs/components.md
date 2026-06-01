# Component Mapping

## Side-by-Side: Every Original Component → Azure Equivalent

```mermaid
flowchart LR
    subgraph "Docker/Kafka Stack"
        A1["APScheduler<br/>BackgroundScheduler<br/>IntervalTrigger"]
        A2["RSSFetcher<br/>httpx.AsyncClient<br/>feedparser<br/>semaphore=50"]
        A3["KafkaPublisher<br/>kafka-python<br/>KafkaProducer"]
        A4["StorageManager<br/>local JSONL files<br/>100 MB compaction"]
        A5["Validator<br/>title · link · published<br/>dead-letter topic"]
        A6["FastAPI + Uvicorn<br/>0.0.0.0:8000<br/>Prometheus metrics"]
        A7["Prometheus<br/>prometheus.yml<br/>scrape /metrics"]
        A8["Grafana<br/>dashboards<br/>admin/admin"]
        A9["FeedManager<br/>feeds.json<br/>file-based cache"]
    end

    subgraph "Azure Serverless Stack"
        B1["TimerTrigger<br/>function.json CRON<br/>built-in retry"]
        B2["RSSFetcher<br/>same httpx + feedparser<br/>same semaphore=50"]
        B3["QueuePublisher<br/>azure-storage-queue<br/>QueueClient"]
        B4["BlobManager<br/>azure-storage-blob<br/>BlobClient"]
        B5["Validator<br/>same validation logic<br/>dead-letter queue"]
        B6["HTTPTrigger<br/>HealthEndpoint<br/>App Insights metrics"]
        B7["App Insights<br/>auto-instrumented<br/>KQL dashboards"]
        B8["Azure Dashboards<br/>pin KQL queries<br/>RBAC access"]
        B9["FeedManager<br/>same feeds.json<br/>package data"]
    end

    A1 -->|"replaced by"| B1
    A2 -->|"preserved core<br/>new wrappers"| B2
    A3 -->|"replaced by"| B3
    A4 -->|"replaced by"| B4
    A5 -->|"preserved logic<br/>new sink"| B5
    A6 -->|"replaced by"| B6
    A7 -->|"replaced by"| B7
    A8 -->|"replaced by"| B8
    A9 -->|"preserved<br/>new source"| B9
```

## Detailed Component Specs

### 1. RSSFetcher Function

```
Type:           TimerTrigger
Schedule:       0 */5 * * * * (every 5 minutes)
Memory:         1.5 GB (consumption plan)
Timeout:        10 minutes (functionTimeout in host.json)
Max concurrency: 50 simultaneous HTTP connections (semaphore)
Retry:          Built-in (function runtime retries on exception)
```

```mermaid
sequenceDiagram
    participant T as Timer (CRON)
    participant F as RSSFetcher
    participant FM as FeedManager
    participant R as RSS Sources
    participant V as Validator
    participant Q as QueuePublisher
    participant B as BlobManager
    participant TB as TableManager

    T->>F: trigger
    F->>FM: get_active_feeds()
    FM-->>F: 800+ feed dicts

    par For each feed (semaphore=50)
        F->>R: GET with ETag
        alt 304
            R-->>F: skip
        else 200
            R-->>F: XML body
            F->>V: filter_valid_articles()
            V-->>F: valid + invalid
            F->>Q: publish(valid)
            F->>TB: upsert(valid)
            F->>B: save_raw_xml(xml)
            F->>B: save_parsed_json(articles)
        end
    end

    F-->>T: complete
```

### 2. HealthEndpoint Function

```
Type:           HTTPTrigger
Auth:           Anonymous (or Function key)
Methods:        GET
Route:          api/health
```

**Response:**
```json
{
  "status": "healthy",
  "timestamp": "2025-06-01T12:00:00Z",
  "last_poll": "2025-06-01T11:55:00Z",
  "feeds_configured": 823,
  "feeds_active": 672,
  "storage_connected": true,
  "region": "southafricanorth"
}
```

### 3. QueuePublisher

```
SDK:            azure-storage-queue
Queue:          article-ingest
Encoding:       Base64 (BinaryBase64EncodePolicy)
Message size:   Max 64 KB (batch articles per feed)
TTL:            7 days (auto-expire if unprocessed)
Poison queue:   article-ingest-poison (auto-created by Functions)
```

```python
class QueuePublisher:
    """Drop-in replacement for KafkaPublisher — same interface, different backend."""

    def __init__(self, conn_str: str, queue_name: str = "article-ingest"):
        self.client = QueueClient.from_connection_string(
            conn_str, queue_name,
            message_encode_policy=BinaryBase64EncodePolicy()
        )

    def publish(self, topic: str, message: dict) -> bool:
        # topic parameter kept for interface compatibility; unused with Queue Storage
        return self._send(message)

    def publish_batch(self, messages: list[dict]) -> int:
        # Batch up to 64 KB — ~50 articles per message
        payload = json.dumps(messages, ensure_ascii=False)
        encoded = BinaryBase64EncodePolicy().encode(payload.encode('utf-8'))
        self.client.send_message(encoded)
        return len(messages)
```

### 4. BlobManager

```
SDK:            azure-storage-blob
Container:      news-data
Structure:
  raw-feeds/{feed_name}.xml
  parsed-articles/{feed_name}/{timestamp}.json
  dead-letter/{feed_name}/{timestamp}.json
  logs/{date}.log
```

```python
class BlobManager:
    """Drop-in replacement for StorageManager — same interface."""

    def __init__(self, conn_str: str, container: str = "news-data"):
        self.client = BlobServiceClient.from_connection_string(conn_str)
        self.container_name = container
        self._ensure_container()

    def save_raw_feed(self, feed_content: str, feed_name: str) -> str:
        blob_name = f"raw-feeds/{feed_name}.xml"
        self.client.get_blob_client(container=self.container_name, blob=blob_name) \
            .upload_blob(feed_content, overwrite=True)
        return blob_name

    def save_parsed_articles(self, articles: list, feed_name: str) -> str:
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
        blob_name = f"parsed-articles/{feed_name}/{ts}.json"
        payload = json.dumps(articles, ensure_ascii=False, indent=2)
        self.client.get_blob_client(container=self.container_name, blob=blob_name) \
            .upload_blob(payload, overwrite=True)
        return blob_name

    def save_dead_letter(self, articles: list, feed_name: str) -> str:
        ts = datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")
        blob_name = f"dead-letter/{feed_name}/{ts}.json"
        payload = json.dumps(articles, ensure_ascii=False, indent=2)
        self.client.get_blob_client(container=self.container_name, blob=blob_name) \
            .upload_blob(payload, overwrite=True)
        return blob_name
```

### 5. TableManager

```
SDK:            azure-data-tables
Table:          ArticleIndex
PartitionKey:   date (YYYY-MM-DD) — enables date-range queries
RowKey:         SHA-256 of article link (first 32 chars) — unique per article
Columns:        Title, Source, Link, Published, Category, ProcessedAt
```

```python
class TableManager:
    """Key-value article index for querying by date and deduplication."""

    def __init__(self, conn_str: str, table_name: str = "ArticleIndex"):
        self.client = TableServiceClient.from_connection_string(conn_str)
        self.table = self.client.create_table_if_not_exists(table_name)
        self.table_name = table_name

    def upsert_article(self, article: dict) -> None:
        entity = {
            "PartitionKey": self._date_key(article.get("published", "")),
            "RowKey": self._hash_key(article.get("link", "")),
            "Title": (article.get("title") or "")[:255],
            "Source": article.get("source", ""),
            "Link": article.get("link", ""),
            "Published": article.get("published", ""),
            "Summary": (article.get("summary") or "")[:1024],
        }
        self.table.upsert_entity(entity)

    def query_by_date(self, date_str: str) -> list[dict]:
        entities = self.table.query_entities(f"PartitionKey eq '{date_str}'")
        return list(entities)

    @staticmethod
    def _date_key(published: str) -> str:
        return published[:10] if published else datetime.utcnow().strftime("%Y-%m-%d")

    @staticmethod
    def _hash_key(link: str) -> str:
        return hashlib.sha256(link.encode()).hexdigest()[:32]
```

### 6. Function App Configuration

```json
// host.json
{
  "version": "2.0",
  "functionTimeout": "00:10:00",
  "logging": {
    "applicationInsights": {
      "samplingSettings": {
        "isEnabled": true,
        "excludedTypes": "Request"
      }
    },
    "logLevel": {
      "default": "Warning",
      "Function.RSSFetcher": "Information"
    }
  },
  "extensions": {
    "queues": {
      "maxPollingInterval": "00:00:02",
      "visibilityTimeout": "00:01:00",
      "batchSize": 16,
      "maxDequeueCount": 5,
      "newBatchThreshold": 8
    }
  },
  "concurrency": {
    "dynamicConcurrencyEnabled": true,
    "snapshotPersistenceEnabled": true
  }
}
```

### 7. Identity & Access

```mermaid
flowchart TB
    subgraph "Function App"
        MI["System-Assigned<br/>Managed Identity"]
    end

    subgraph "RBAC Assignments"
        R1["Storage Blob Data Contributor<br/>→ news-data container"]
        R2["Storage Queue Data Contributor<br/>→ article-ingest queue"]
        R3["Storage Table Data Contributor<br/>→ ArticleIndex table"]
    end

    MI --> R1
    MI --> R2
    MI --> R3
```

No connection strings in code. The Function App authenticates to Storage via its Managed Identity. In `local.settings.json` for development, use Azurite's well-known connection string.

### 8. Environment Variables

```bash
# local.settings.json (dev with Azurite)
{
  "IsEncrypted": false,
  "Values": {
    "AzureWebJobsStorage": "UseDevelopmentStorage=true",
    "FUNCTIONS_WORKER_RUNTIME": "python",
    "STORAGE_ACCOUNT_URL": "UseDevelopmentStorage=true",
    "QUEUE_NAME": "article-ingest",
    "CONTAINER_NAME": "news-data",
    "TABLE_NAME": "ArticleIndex",
    "FEED_POLL_BATCH_SIZE": "50",
    "REQUEST_TIMEOUT": "10",
    "RATE_LIMIT_DELAY": "1"
  }
}
```

```bash
# Production (set via Azure Portal / AZ CLI)
AzureWebJobsStorage=DefaultEndpointsProtocol=https;AccountName=...;EndpointSuffix=core.windows.net
STORAGE_ACCOUNT_URL=https://stnewsaggregator.blob.core.windows.net
QUEUE_NAME=article-ingest
CONTAINER_NAME=news-data
TABLE_NAME=ArticleIndex
```
