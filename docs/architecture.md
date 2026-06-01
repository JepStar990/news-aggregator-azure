# Architecture

## Overview

The Azure-native pipeline replaces the original self-hosted Docker/Kafka stack with fully-managed Azure services. The architecture follows a **serverless event-driven** pattern where every component scales independently and costs are proportional to usage — not uptime.

## High-Level Architecture

```mermaid
flowchart TB
    subgraph Sources["800+ RSS Feeds"]
        S1[BBC]
        S2[CNN]
        S3[Reuters]
        S4[...]
    end

    subgraph Compute["Azure Functions · Consumption Plan"]
        F1["TimerTrigger<br/>RSSFetcher<br/>(every 5 min)"]
        F2["HTTPTrigger<br/>HealthEndpoint<br/>(/api/health)"]
    end

    subgraph Messaging["Azure Storage Account"]
        QS[("Queue Storage<br/>article-ingest")]
        BS[("Blob Storage<br/>raw-feeds<br/>parsed-articles<br/>logs")]
        TS[("Table Storage<br/>ArticleIndex")]
    end

    subgraph Observability["Monitoring"]
        AI[("Application Insights<br/>logs · metrics · alerts")]
    end

    Sources -->|HTTP GET · ETag · If-Modified-Since| F1
    F1 -->|valid articles| QS
    F1 -->|raw XML · JSON| BS
    F1 -->|article metadata| TS
    F1 -.->|telemetry| AI
    F2 -.->|telemetry| AI
```

## Request Flow

```mermaid
sequenceDiagram
    participant Timer as TimerTrigger
    participant Fetcher as RSSFetcher Function
    participant RSS as RSS Source
    participant Validator as ContentValidator
    participant Queue as Queue Storage
    participant Blob as Blob Storage
    participant Table as Table Storage
    participant AI as App Insights

    Timer->>Fetcher: Trigger (CRON)
    loop For each feed
        Fetcher->>RSS: GET /feed.xml<br/>(If-None-Match, If-Modified-Since)
        alt 304 Not Modified
            RSS-->>Fetcher: 304
        else 200 OK
            RSS-->>Fetcher: 200 + XML
            Fetcher->>Validator: validate(articles)
            loop For each valid article
                Fetcher->>Queue: enqueue(article)
                Fetcher->>Table: upsert(article metadata)
            end
            Fetcher->>Blob: save_raw_xml(feed)
            Fetcher->>Blob: save_parsed_json(articles)
            Fetcher-.->>AI: trackMetric(articles_processed)
            Fetcher-.->>AI: trackDependency(RSS_fetch)
        end
    end
```

## Component Deep-Dive

```mermaid
flowchart LR
    subgraph "Function App"
        direction TB
        A["__init__.py<br/>TimerTrigger entry"]
        B["rss_fetcher.py<br/>fetch / parse / validate"]
        C["validator.py<br/>title · link · published"]
        D["feed_manager.py<br/>800+ feeds.json"]
        E["config.py<br/>env → Key Vault refs"]
    end

    subgraph "Storage SDK"
        F["queue_publisher.py<br/>QueueClient.send_message()"]
        G["blob_manager.py<br/>BlobClient.upload_blob()"]
        H["table_manager.py<br/>TableClient.upsert_entity()"]
    end

    A --> B
    B --> C
    B --> D
    B --> E
    C --> F
    C --> G
    C --> H
```

## Deployment Architecture

```mermaid
flowchart TB
    subgraph "GitHub"
        GH[GitHub Actions]
    end

    subgraph "Azure Subscription"
        subgraph "Resource Group: rg-news-aggregator"
            FA["Function App<br/>(Python 3.11 · Linux)"]
            SA["Storage Account<br/>(LRS · Hot)"]
            AI2["Application Insights"]
        end
    end

    GH -->|"func azure functionapp publish<br/>or Azure/functions-action"| FA
    FA --> SA
    FA -.-> AI2
```

## Key Design Decisions

### Why Queue Storage instead of Event Hubs / Service Bus?

| Service | Cost | Throughput | Why/Why Not |
|---|---|---|---|
| **Queue Storage** | $0.04/10K ops | 2,000 msg/sec | Perfect for <10 msg/sec at 800 feeds |
| Event Hubs Basic | $11/mo | 1 MB/sec ingress | Overkill at this scale |
| Service Bus Basic | $0.05/mo + per-op | 1,500 msg/sec | Adds complexity (topics, sessions) we don't need |

### Why Blob Storage instead of Cosmos DB?

Blob Storage is the right sink for raw XML and parsed JSON because:
- **No query pattern** — the data is write-once, read-rarely
- **$.018/GB** vs Cosmos DB at $23.36/100 RU/s minimum
- **Lifecycle management** — auto-tier to Archive after 90 days ($0.00099/GB)

### Why Table Storage instead of Cosmos DB or SQL?

Table Storage provides:
- **Key-value lookups by date/hash** — PartitionKey on `date`, RowKey on `article_hash`
- **$.07/10K transactions** — 100x cheaper than Cosmos DB
- **No schema enforcement** — flexible columns per article

### Why Functions Consumption instead of Premium / App Service?

- **1M free executions/month** covers the baseline workload
- **Scales to zero** — no cost when idle (between polls)
- **Cold start (<2s)** is acceptable for a 5-minute polling interval

## Cold Start Strategy

```mermaid
flowchart LR
    A[Timer fires] --> B{Cold?}
    B -->|yes| C["~1.5s cold start<br/>(Python 3.11 · Linux)"]
    B -->|no| D["~50ms warm"]
    C --> E["Parallel fetch<br/>800 feeds · semaphore=50"]
    D --> E
    E --> F["Complete in ~30s"]
```

With a 5-minute polling interval and ~30-second execution time, cold starts are irrelevant — the function stays warm across most invocations.

## Security Model

```mermaid
flowchart TB
    subgraph "Identity"
        MI["Managed Identity<br/>(System-assigned)"]
    end

    subgraph "Storage Account"
        SA2["Blob · Queue · Table"]
    end

    subgraph "Configuration"
        KV["Key Vault<br/>(optional)"]
    end

    MI -->|"Storage Blob Data Contributor<br/>Storage Queue Data Contributor<br/>Storage Table Data Contributor"| SA2
    MI -.->|optional: secret references| KV
```

- **No connection strings in code** — Managed Identity + RBAC
- **No API keys in `.env`** — Function app settings reference Key Vault
- **Network security** — Storage account firewalled to Azure services (optional)
