# Industrial Anomaly Detection Client

React, TypeScript, and Vite frontend for the Industrial Anomaly Detection System.

## Development

```sh
npm install
npm run dev
```

The production checks are `npm run lint` and `npm run build`.

## Source layout

- `src/App.tsx` coordinates authentication, workspace data, navigation, and page rendering.
- `src/pages/` contains the individual workspace pages and their page-specific workflows.
- `src/components/` contains reusable presentation components and shared form styles.
- `src/api.ts` handles authenticated API requests and downloads from FastAPI.
- `src/datasetFeatures.ts` contains shared dataset feature-selection rules.
- `src/types.ts` and `src/types/workflow.ts` define the application and page data contracts.

`src/pages/WorkflowPages.ts` is a small export barrel for the secondary pages.
