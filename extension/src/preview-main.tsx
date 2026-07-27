import React from 'react';
import ReactDOM from 'react-dom/client';

import { PreviewWindow } from './PreviewWindow';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <PreviewWindow />
  </React.StrictMode>,
);
