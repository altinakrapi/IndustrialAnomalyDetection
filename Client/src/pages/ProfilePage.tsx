import { buttonClass, panelClass, Field } from '../components/ui'
import type { FormEvent } from 'react'
import { request } from '../api'

import type { Action, User } from '../types/workflow'

import { PageTitle } from '../components/PageTitle'

export function ProfilePage({
  user,
  onUserUpdate,
  onAction,
}: {
  user: User
  onUserUpdate: (user: User) => void
  onAction: Action
}) {
  async function updateProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const values = Object.fromEntries(new FormData(event.currentTarget))
    await onAction(async () => {
      const updated = await request<User>('/auth/me', {
        method: 'PATCH',
        body: JSON.stringify(values),
      })
      onUserUpdate(updated)
    }, 'Profile updated')
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = event.currentTarget
    const values = new FormData(form)
    if (values.get('new_password') !== values.get('confirm_password')) {
      window.alert('The new passwords do not match.')
      return
    }
    await onAction(async () => {
      await request('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: values.get('current_password'),
          new_password: values.get('new_password'),
        }),
      })
      form.reset()
    }, 'Password changed')
  }

  return (
    <>
      <PageTitle title="Personal profile" description="Manage the details and password for your account." />
      <form className={`${panelClass} grid gap-4 sm:grid-cols-2`} onSubmit={updateProfile}>
        <Field label="Full name" name="full_name" defaultValue={user.full_name} required />
        <Field label="Email address" name="email" defaultValue={user.email} required type="email" />
        <p className="text-xs text-slate-500">Role: {user.role.replaceAll('_', ' ')}</p>
        <button className={buttonClass} type="submit">Save profile</button>
      </form>
      <form className={`${panelClass} grid gap-4 sm:grid-cols-2`} onSubmit={changePassword}>
        <h2 className="text-sm font-semibold text-slate-900 sm:col-span-2">Change password</h2>
        <Field label="Current password" name="current_password" required type="password" />
        <Field label="New password (at least 10 characters)" name="new_password" required type="password" minLength={10} />
        <Field label="Confirm new password" name="confirm_password" required type="password" minLength={10} />
        <button className={buttonClass} type="submit">Update password</button>
      </form>
    </>
  )
}
