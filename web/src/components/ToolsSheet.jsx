import Connectors from '../screens/Connectors';
import Sheet from './Sheet';

/** Connect an app from anywhere: the same picker, in a sheet. */
export default function ToolsSheet({ onClose, back = null }) {
  return (
    <Sheet title="Connect a tool" tall onClose={onClose} back={back}>
      <Connectors embedded />
    </Sheet>
  );
}
