import { AvatarEditor } from '../components/AvatarEditor'

export default function AvatarView() {
  return (
    <div className="h-full overflow-y-auto bg-gradient-to-br from-stone-50 to-violet-50 p-4 text-slate-800">
      <div className="mx-auto max-w-5xl">
        <div className="mb-4">
          <h1 className="text-xl font-semibold">Avatar</h1>
          <p className="text-sm text-slate-500">Personaliza cómo se presenta tu SEAL.</p>
        </div>
        <AvatarEditor previewSize={320} />
      </div>
    </div>
  )
}

